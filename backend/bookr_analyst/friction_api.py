"""HTTP API for the Friction Finder (mounted by api.py under /api/friction)."""
from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .config import settings
from .db import connect, rows
from .friction import judge as J
from .friction import runner
from .friction import issues as IS
from .friction import taxonomy as TX
from .friction.export import GROUPS, build_example, dataset_items, finetune_candidates, write_export
from .friction.signals import config as fconfig
from .friction.store import all_dicts, as_dict, conn, load_export_file, now_iso

router = APIRouter(prefix="/api/friction")
OFF = int(settings.tz_offset_minutes)


def today_ist() -> date:
    return (datetime.now(timezone.utc) + timedelta(minutes=OFF)).date()


@router.get("/status")
def status():
    cfg = fconfig()
    try:
        with connect() as con:
            # a chat belongs to the IST day of its last user message (same rule as the runner)
            days = rows(con, """
                WITH s AS (SELECT m.chat_session_id, CAST(max(m.created_local) AS DATE) AS day, count(*) AS n
                           FROM messages m JOIN session_facts sf ON sf.session_id = m.chat_session_id
                           WHERE m.role = 'USER' AND (NOT $x OR NOT sf.is_internal) GROUP BY 1)
                SELECT day, count(*) AS chats, sum(n) AS user_messages FROM s
                GROUP BY 1 ORDER BY 1 DESC LIMIT 60""", {"x": cfg.get("exclude_internal_users", True)})
    except FileNotFoundError:
        days = []
    with conn() as c:
        runs = {r["day"]: dict(r) for r in c.execute(
            "SELECT * FROM runs WHERE id IN (SELECT max(id) FROM runs GROUP BY day)")}
        rev = {r["day"]: dict(r) for r in c.execute("""
            SELECT f.day, count(DISTINCT f.id) AS findings, count(i.id) AS items,
                   sum((i.verdict = 'pending')::int) AS pending, sum((i.verdict = 'agree')::int) AS agreed
            FROM findings f LEFT JOIN items i ON i.finding_id = f.id GROUP BY f.day""")}
        latest = as_dict(c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone())
    for d in days:
        d["run"] = runs.get(d["day"])
        d["review"] = rev.get(d["day"])
    return {"llm_enabled": J.llm_enabled(), "model": J.model_name(), "provider": J.provider(),
            "provider_label": J.provider_label(), "claude_cli": J._claude_cli(), "running": runner.state()["running"],
            "latest_run": latest, "daily_at": settings.friction_daily_at or None, "today_ist": today_ist().isoformat(),
            "days": days, "mandatory_fields": {k: v for k, v in cfg["mandatory_fields"].items() if not k.startswith("_")},
            "types": J.FRICTION_TYPES, "taxonomy": TX.describe(),
            "finetune_cluster_min": cfg["thresholds"].get("finetune_cluster_min", 20)}


class RunReq(BaseModel):
    day: str
    use_llm: bool | None = None


@router.post("/runs")
def start(req: RunReq):
    try:
        d = datetime.strptime(req.day, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    if req.use_llm and not J.llm_enabled():
        raise HTTPException(400, "LLM judge is not configured: set ANTHROPIC_API_KEY in .env, "
                                 "or install Claude Code and log in with your Claude subscription")
    try:
        run_id = runner.start_run(d, req.use_llm, "ui")
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"run_id": run_id}


@router.get("/runs")
def list_runs(limit: int = 30):
    with conn() as c:
        return all_dicts(c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall())


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    with conn() as c:
        r = as_dict(c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
    if not r:
        raise HTTPException(404)
    return r


FINDING_LIST = """
    SELECT f.id, f.day, f.session_id, f.user_id, f.title, f.flow, f.session_started_local, f.n_user_msgs, f.outcome,
           f.score, f.source, f.summary, f.submitted_at, f.submitted_by,
           count(i.id) AS n_items,
           sum((i.verdict = 'pending')::int) AS n_pending, sum((i.verdict = 'agree')::int) AS n_agree,
           sum((i.verdict = 'not_friction')::int) AS n_not_friction, sum((i.verdict = 'skip')::int) AS n_skip,
           string_agg(DISTINCT i.type, ',') AS types,
           string_agg(DISTINCT i.bucket, ',') AS buckets,
           string_agg(DISTINCT i.fix_lane, ',') AS lanes,
           max(CASE i.severity WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) AS sev_rank
    FROM findings f LEFT JOIN items i ON i.finding_id = f.id
"""


@router.get("/findings")
def findings(day: str | None = None, status: str | None = None, type: str | None = None,
             bucket: str | None = None, lane: str | None = None):
    where, p = ["1=1"], []
    if day:
        where.append("f.day = ?")
        p.append(day)
    if type:
        where.append("EXISTS (SELECT 1 FROM items x WHERE x.finding_id = f.id AND x.type = ?)")
        p.append(type)
    if bucket:
        where.append("EXISTS (SELECT 1 FROM items x WHERE x.finding_id = f.id AND x.bucket = ?)")
        p.append(bucket)
    if lane:
        where.append("EXISTS (SELECT 1 FROM items x WHERE x.finding_id = f.id AND x.fix_lane = ?)")
        p.append(lane)
    n_pending, n_agree = "sum((i.verdict = 'pending')::int)", "sum((i.verdict = 'agree')::int)"
    having = {"pending": f"HAVING {n_pending} > 0", "reviewed": f"HAVING {n_pending} = 0 AND f.submitted_at IS NULL",
              "submitted": "HAVING f.submitted_at IS NOT NULL", "agreed": f"HAVING {n_agree} > 0"}.get(status or "", "")
    with conn() as c:
        out = [dict(r) for r in c.execute(
            f"{FINDING_LIST} WHERE {' AND '.join(where)} GROUP BY f.id {having} ORDER BY sev_rank DESC NULLS LAST, f.score DESC NULLS LAST, f.id",
            p).fetchall()]
    sev = {3: "high", 2: "medium", 1: "low"}
    for r in out:
        r["types"] = sorted((r["types"] or "").split(",")) if r["types"] else []
        order = list(TX.BUCKETS)
        r["buckets"] = sorted((r["buckets"] or "").split(","), key=lambda b: order.index(b) if b in order else 9) \
            if r["buckets"] else []
        r["lanes"] = sorted((r["lanes"] or "").split(",")) if r["lanes"] else []
        r["severity"] = sev.get(r.pop("sev_rank") or 1)
        r["status"] = ("submitted" if r["submitted_at"] else "pending" if r["n_pending"] else "reviewed")
    return out


@router.get("/findings/{fid}")
def finding(fid: int):
    with conn() as c:
        f = as_dict(c.execute("SELECT * FROM findings WHERE id = ?", (fid,)).fetchone())
        if not f:
            raise HTTPException(404, "finding not found")
        f["items"] = all_dicts(c.execute("SELECT * FROM items WHERE finding_id = ? ORDER BY id", (fid,)).fetchall())
        queue = [r[0] for r in c.execute("""
            SELECT f.id FROM findings f WHERE f.day = ? AND f.submitted_at IS NULL
              AND EXISTS (SELECT 1 FROM items i WHERE i.finding_id = f.id AND i.verdict = 'pending')
            ORDER BY f.score DESC NULLS LAST, f.id""", (f["day"],))]
    f["next_pending_id"] = next((x for x in queue if x != fid), None)
    f["pending_in_day"] = len(queue)
    return f


class ItemReview(BaseModel):
    id: int
    verdict: str
    note: str | None = None
    corrected_interpretation: str | None = None
    corrected_guidance: str | None = None
    bucket: str | None = None        # R8: reviewer may correct the sorting
    owner: str | None = None
    fix_lane: str | None = None      # None = recompute from bucket and owner
    lesson_is_rule: bool | None = None


class ReviewReq(BaseModel):
    reviewer: str
    items: list[ItemReview]
    submit: bool = False


@router.post("/findings/{fid}/review")
def review(fid: int, req: ReviewReq):
    if not req.reviewer.strip():
        raise HTTPException(400, "Enter your name so reviews can be attributed")
    with conn() as c:
        cur_items = {r["id"]: dict(r) for r in c.execute(
            "SELECT id, type, bucket, owner, fix_lane, served_model, lesson_is_rule FROM items WHERE finding_id = ?", (fid,))}
        ids = set(cur_items)
        if not ids:
            raise HTTPException(404, "finding not found")
        for it in req.items:
            if it.id not in ids:
                raise HTTPException(400, f"item {it.id} is not part of finding {fid}")
            if it.verdict not in ("pending", "agree", "not_friction", "skip"):
                raise HTTPException(400, "verdict must be agree, not_friction, skip or pending")
            old = cur_items[it.id]
            if it.bucket is not None and it.bucket not in TX.BUCKETS:
                raise HTTPException(400, f"bucket must be one of {', '.join(TX.BUCKETS)}")
            if it.owner is not None and it.owner not in TX.OWNER_KIND:
                raise HTTPException(400, "unknown owner")
            if it.fix_lane is not None and it.fix_lane not in TX.FIX_LANES:
                raise HTTPException(400, f"fix_lane must be one of {', '.join(TX.FIX_LANES)}")
            bucket = it.bucket or old["bucket"]
            owner = it.owner or old["owner"]
            rule = old["lesson_is_rule"] if it.lesson_is_rule is None else int(it.lesson_is_rule)
            lane = it.fix_lane or (old["fix_lane"] if (bucket, owner, rule) == (old["bucket"], old["owner"], old["lesson_is_rule"])
                                   else TX.fix_lane(bucket, owner, old["served_model"], bool(rule)))
            edited = (bucket, owner, lane, rule) != (old["bucket"], old["owner"], old["fix_lane"], old["lesson_is_rule"])
            if edited:
                c.execute("UPDATE items SET bucket = ?, owner = ?, fix_lane = ?, lesson_is_rule = ?, routing_edited = 1 "
                          "WHERE id = ?", (bucket, owner, lane, rule, it.id))
            c.execute("""UPDATE items SET verdict = ?, review_note = ?, corrected_interpretation = ?, corrected_guidance = ?,
                         reviewer = ?, reviewed_at = ? WHERE id = ?""",
                      (it.verdict, it.note or None, it.corrected_interpretation or None, it.corrected_guidance or None,
                       req.reviewer.strip() if it.verdict != "pending" else None,
                       now_iso() if it.verdict != "pending" else None, it.id))
        if req.submit:
            pending = c.execute("SELECT count(*) FROM items WHERE finding_id = ? AND verdict = 'pending'", (fid,)).fetchone()[0]
            if pending:
                raise HTTPException(400, f"{pending} item(s) still need a verdict before submitting")
            c.execute("UPDATE findings SET submitted_at = ?, submitted_by = ? WHERE id = ?",
                      (now_iso(), req.reviewer.strip(), fid))
        else:
            c.execute("UPDATE findings SET submitted_at = NULL, submitted_by = NULL WHERE id = ?", (fid,))
    return finding(fid)


@router.get("/summary")
def summary(day: str | None = None):
    where, p = ("WHERE f.day = ?", [day]) if day else ("", [])
    with conn() as c:
        by_type = [dict(r) for r in c.execute(f"""
            SELECT i.type, count(*) AS items, count(DISTINCT i.finding_id) AS chats,
                   sum((i.verdict = 'agree')::int) AS agreed, sum((i.verdict = 'not_friction')::int) AS not_friction,
                   sum((i.verdict = 'pending')::int) AS pending
            FROM items i JOIN findings f ON f.id = i.finding_id {where} GROUP BY 1 ORDER BY 2 DESC""", p)]
        for r in by_type:
            r["bucket"] = TX.TYPES.get(r["type"], (None, ""))[0]
        by_bucket = {r["bucket"]: dict(r) for r in c.execute(f"""
            SELECT i.bucket, count(*) AS items, count(DISTINCT i.finding_id) AS chats,
                   sum((i.verdict = 'agree')::int) AS agreed, sum((i.verdict = 'not_friction')::int) AS not_friction,
                   sum((i.verdict = 'pending')::int) AS pending
            FROM items i JOIN findings f ON f.id = i.finding_id {where} GROUP BY 1""", p)}
        by_lane = [dict(r) for r in c.execute(f"""
            SELECT i.fix_lane, count(*) AS items, sum((i.verdict = 'agree')::int) AS agreed
            FROM items i JOIN findings f ON f.id = i.finding_id {where} GROUP BY 1 ORDER BY 2 DESC""", p)]
        totals = dict(c.execute(f"""
            SELECT count(DISTINCT f.id) AS findings, count(i.id) AS items,
                   coalesce(sum((i.verdict = 'pending')::int), 0) AS pending, coalesce(sum((i.verdict = 'agree')::int), 0) AS agreed,
                   coalesce(sum((i.verdict = 'not_friction')::int), 0) AS not_friction, coalesce(sum((i.verdict = 'skip')::int), 0) AS skipped,
                   count(DISTINCT CASE WHEN f.submitted_at IS NOT NULL THEN f.id END) AS submitted
            FROM findings f LEFT JOIN items i ON i.finding_id = f.id {where}""", p).fetchone())
        run = as_dict(c.execute("SELECT * FROM runs WHERE day = ? ORDER BY id DESC LIMIT 1", (day,)).fetchone()) if day else None
    decided = totals["agreed"] + totals["not_friction"]
    totals["precision_pct"] = round(100 * totals["agreed"] / decided, 1) if decided else None
    buckets = [{"bucket": b, "label": TX.BUCKET_LABEL[b],
                **{k: by_bucket.get(b, {}).get(k) or 0 for k in ("items", "chats", "agreed", "not_friction", "pending")}}
               for b in TX.BUCKETS]
    for b in buckets:
        d = b["agreed"] + b["not_friction"]
        b["precision_pct"] = round(100 * b["agreed"] / d, 1) if d else None
    for lr in by_lane:
        lr["label"] = TX.FIX_LANE_LABEL.get(lr["fix_lane"], lr["fix_lane"])
    return {"totals": totals, "by_type": by_type, "by_bucket": buckets, "by_lane": by_lane, "run": run}


@router.get("/dataset")
def dataset():
    items = dataset_items()
    examples = [build_example(i) for i in items]
    with conn() as c:
        exports = [dict(r) for r in c.execute("SELECT * FROM exports ORDER BY id DESC LIMIT 20")]
        by_reviewer = [dict(r) for r in c.execute("""SELECT reviewer, count(*) AS reviewed,
                         sum((verdict = 'agree')::int) AS agreed FROM items WHERE verdict <> 'pending' GROUP BY 1 ORDER BY 2 DESC""")]
        pending = c.execute("SELECT count(*) FROM items WHERE verdict = 'pending'").fetchone()[0]
    types: dict[str, int] = {}
    for e in examples:
        types[e["friction_type"]] = types.get(e["friction_type"], 0) + 1
    for x in exports:
        for k in ("files", "counts"):
            x[k] = json.loads(x[k]) if x.get(k) else None
    groups = [{"group": k, "label": g["label"], "count": sum(1 for e in examples if e["group"] == k)} for k, g in GROUPS.items()]
    last_export = exports[0]["created_at"] if exports else None
    new_since = sum(1 for i in items if last_export and (i.get("reviewed_at") or "") > last_export) if last_export else len(items)
    return {"examples": examples, "by_type": types, "groups": groups, "exports": exports, "by_reviewer": by_reviewer,
            "pending_items": pending, "new_since_last_export": new_since,
            "finetune_candidates": finetune_candidates(examples), "lora_signals": _lora_signals(),
            "rules": sorted({e["guidance"].strip() for e in examples if e["group"] != "engineering" and e["guidance"].strip()})}


def _lora_signals(days: int = 30) -> dict:
    """Fine-tune hints straight from LlmCallLog: how often the LoRA extractor served, failed and why."""
    try:
        with connect() as con:
            r = rows(con, f"""
                SELECT model, count(*) AS calls, sum((NOT success)::INT) AS failed,
                       sum((NOT success AND coalesce(error, '') ~* 'parse|invalid_enum|schema|JSON')::INT)
                         AS structural
                FROM raw_llm_calls
                WHERE task IN ('unified_intent_extraction', 'intent_extraction')
                  AND created_at >= (SELECT max(created_at) FROM raw_llm_calls) - INTERVAL '{int(days)} days'
                GROUP BY 1 ORDER BY 2 DESC""")
    except FileNotFoundError:
        return {"days": days, "models": []}
    for m in r:
        m["is_lora"] = TX.is_lora(m["model"])
    return {"days": days, "models": r}


@router.post("/export")
def export(body: dict = Body(default={})):
    return write_export((body or {}).get("reviewer"))


@router.get("/exports/{name}")
def download(name: str):
    media_type = "application/jsonl" if name.endswith(".jsonl") else "text/markdown"
    f = (settings.exports_dir / name).resolve()
    if f.parent == settings.exports_dir.resolve() and f.is_file():
        return FileResponse(f, filename=name, media_type=media_type)
    content = load_export_file(name)  # written on another machine: served from the database copy
    if content is None:
        raise HTTPException(404)
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# Issue register, priorities, briefs, RAG pack (phase 3c) ---------------------

PERIODS = ("day", "last7", "week", "month")


def _anchor(start: str | None, period: str = "day") -> date:
    """Anchor day for a period. `start` is the period's first day; without it, the latest analysed day."""
    if period not in PERIODS:
        raise HTTPException(400, "period must be day, last7, week or month")
    if start:
        try:
            return IS.anchor_for(period, datetime.strptime(start, "%Y-%m-%d").date())
        except ValueError:
            raise HTTPException(400, "start must be YYYY-MM-DD")
    days = [d for p in IS.periods()["day"] for d in [p["start"]]]
    return date.fromisoformat(days[0]) if days else today_ist() - timedelta(days=1)


@router.get("/periods")
def list_periods():
    return {**IS.periods(), "grouping": IS.state()}


@router.get("/report")
def report(period: str = "week", start: str | None = None):
    rep = IS.period_report(period, _anchor(start, period))
    rep["brief"] = IS.latest_brief(period, rep["start"])
    rep["grouping"] = IS.state()
    rep["llm_enabled"] = J.llm_enabled()
    return rep


@router.post("/issues/group")
def group_issues():
    if not J.llm_enabled():
        raise HTTPException(400, "LLM is not configured")
    return {"started": IS.assign_in_background(), **IS.state()}


@router.get("/issues/{iid}")
def get_issue(iid: int):
    s = IS.issue_detail(iid)
    if not s:
        raise HTTPException(404, "issue not found")
    return s


@router.patch("/issues/{iid}")
def patch_issue(iid: int, body: dict = Body(default={})):
    try:
        s = IS.update_issue(iid, body, (body.get("by") or "").strip() or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not s:
        raise HTTPException(404, "issue not found")
    return s


@router.post("/issues/{iid}/merge")
def merge_issue(iid: int, body: dict = Body(default={})):
    drop = body.get("drop")
    if not isinstance(drop, int) or drop == iid:
        raise HTTPException(400, "drop must be another issue id")
    return IS.merge_issues(iid, drop)


_brief_jobs: dict[str, str] = {}


@router.post("/briefs")
def make_brief(body: dict = Body(default={})):
    period = body.get("period") or "week"
    if not J.llm_enabled():
        raise HTTPException(400, "LLM is not configured")
    anchor = _anchor(body.get("start"), period)
    key = f"{period}:{IS.bounds(period, anchor)[0]}"
    if _brief_jobs.get(key) == "running":
        return {"status": "running"}
    _brief_jobs[key] = "running"

    def job():
        try:
            IS.generate_brief(period, anchor, "ui")
            _brief_jobs[key] = "done"
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            _brief_jobs[key] = f"failed: {str(e)[:200]}"
    threading.Thread(target=job, daemon=True).start()
    return {"status": "running"}


@router.get("/briefs/status")
def brief_status(period: str, start: str):
    s = IS.bounds(period, _anchor(start, period))[0].isoformat()
    return {"status": _brief_jobs.get(f"{period}:{s}"), "brief": IS.latest_brief(period, s)}


@router.post("/rag-pack")
def rag_pack(body: dict = Body(default={})):
    return IS.write_rag_pack(float(body.get("min_confidence", 0.7)), int(body.get("per_issue", 3)))


@router.get("/rag-pack/preview")
def rag_pack_preview(min_confidence: float = 0.7, per_issue: int = 3):
    exs = IS.rag_pack_items(min_confidence, per_issue)
    with conn() as c:
        last = as_dict(c.execute("SELECT * FROM exports WHERE created_by = 'auto (RAG pack)' ORDER BY id DESC LIMIT 1").fetchone())
    by_prompt: dict[str, int] = {}
    for e in exs:
        by_prompt[e["target_prompt"]] = by_prompt.get(e["target_prompt"], 0) + 1
    return {"examples": len(exs), "issues": len({e["issue_id"] for e in exs}),
            "human_agreed": sum(e["review_status"] == "human_agreed" for e in exs),
            "by_prompt": [{"owner": k, "label": TX.OWNER_LABEL.get(k, k), "examples": v} for k, v in by_prompt.items()],
            "last": last}


# Optional daily schedule ----------------------------------------------------

def start_daily_scheduler(sync_fn):
    """If FRICTION_DAILY_AT=HH:MM is set: every day at that IST time, sync data then analyse yesterday."""
    if not settings.friction_daily_at:
        return
    try:
        hh, mm = (int(x) for x in settings.friction_daily_at.split(":"))
    except ValueError:
        print(f"FRICTION_DAILY_AT must be HH:MM, got {settings.friction_daily_at!r}; daily run disabled")
        return

    def loop():
        last = None
        while True:
            now = datetime.now(timezone.utc) + timedelta(minutes=OFF)
            if (now.hour, now.minute) >= (hh, mm) and last != now.date():
                last = now.date()
                day = now.date() - timedelta(days=1)
                with conn() as c:
                    done = c.execute("SELECT 1 FROM runs WHERE day = ? AND status = 'done' AND triggered_by = 'schedule'",
                                     (day.isoformat(),)).fetchone()
                if done or runner.state()["running"]:
                    continue
                try:
                    sync_fn()
                    runner.run_day(day, None, "schedule")
                    if J.llm_enabled():
                        IS.after_daily_run(day)  # group into issues, daily/weekly/monthly brief, refresh RAG pack
                except Exception:
                    traceback.print_exc()
            time.sleep(30)

    threading.Thread(target=loop, daemon=True).start()
