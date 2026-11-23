"""Run the Friction Finder over one day of chats and store findings for review."""
from __future__ import annotations

import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from . import judge as J
from . import signals as S
from . import taxonomy as TX
from .store import conn, now_iso
from .transcript import load_day, render_for_llm

WHY = {
    "repeated_info": "The user had to say the same thing again, so the first message was not fully captured.",
    "misunderstood_intent": "The user had to correct or reject what the assistant understood.",
    "wrong_search": "The search did not match what the user asked for.",
    "user_frustration": "The user told us directly that the answer was not good.",
    "slow_progress": "It took many turns before anything useful (a search) happened.",
    "unnecessary_question": "The assistant asked for an optional detail.",
    "poor_ranking": "The user had to re-filter or re-sort the results themselves.",
    "product_gap": "The user wanted something Bookkr can't do yet.",
    "provider_failure": "The search provider didn't give the user an answer.",
    "llm_call_failure": "A model call failed, so the user got no proper answer.",
    "payment_booking_failure": "The user couldn't complete the payment or booking.",
    "latency": "The user waited a long time for the reply.",
    "app_error": "The user saw an error instead of an answer.",
}


def _served_model(bundle: dict, turns_: list[int]) -> str | None:
    """The model that extracted the intent for the item's user turn: the first successful extractor call after it,
    else the last one before it."""
    calls = [c for c in bundle.get("llm_calls") or []
             if c["success"] and TX.TASK_OWNER.get(c["task"]) == "unified_intent_extraction"]
    if not calls:
        return None
    by_t = {t["t"]: t for t in bundle["turns"]}
    user_ts = [n for n in turns_ if by_t.get(n, {}).get("role") == "USER"]
    if not user_ts:
        return None
    at = by_t[max(user_ts)].get("at_utc")
    after = [c for c in calls if at and c["created_at"] >= at]
    before = [c for c in calls if at and c["created_at"] < at]
    return (after[0] if after else before[-1] if before else calls[-1])["model"]


def _route(it: dict, bundle: dict) -> dict:
    """R8: bucket, owner, served model and fix lane for one item."""
    t = TX.normalize_type(it.get("type"))
    bucket = TX.bucket_for(t, it.get("bucket"))
    owner = TX.normalize_owner(it.get("owner") or TX.DEFAULT_OWNER.get(t))
    served = _served_model(bundle, it.get("turns") or [])
    structural = any(s.get("lora_structural") for s in bundle.get("signals") or [])
    lane = TX.fix_lane(bucket, owner, served, bool(it.get("lesson_is_rule")), structural)
    return {**it, "type": t, "bucket": bucket, "owner": owner, "served_model": served, "fix_lane": lane}

_state = {"running": False, "run_id": None}
_state_lock = threading.Lock()


def state() -> dict:
    return dict(_state)


def start_run(day: date, use_llm: bool | None = None, triggered_by: str = "ui") -> int:
    with _state_lock:
        if _state["running"]:
            raise RuntimeError("A Friction Finder run is already in progress")
        _state["running"] = True
    run_id = _create_run(day, use_llm, triggered_by)
    _state["run_id"] = run_id

    def bg():
        try:
            run_day(day, use_llm, triggered_by, run_id=run_id)
        finally:
            _state["running"] = False

    threading.Thread(target=bg, daemon=True).start()
    return run_id


def _mode(use_llm: bool | None) -> str:
    return "llm" if (J.llm_enabled() if use_llm is None else use_llm) else "rules"


def _create_run(day: date, use_llm: bool | None, triggered_by: str) -> int:
    mode = _mode(use_llm)
    with conn() as c:
        cur = c.execute("INSERT INTO runs (day, started_at, status, mode, model, triggered_by, progress) VALUES (?,?,?,?,?,?,?) RETURNING id",
                        (day.isoformat(), now_iso(), "running", mode,
                         f"{J.model_name()} · {J.provider_label()}" if mode == "llm" else None,
                         triggered_by, "Loading chats…"))
        return cur.fetchone()[0]


def _progress(run_id: int, text: str, **counts):
    sets = ", ".join(f"{k} = ?" for k in counts)
    with conn() as c:
        c.execute(f"UPDATE runs SET progress = ?{', ' + sets if sets else ''} WHERE id = ?",
                  (text, *counts.values(), run_id))


def _rules_items(sig: list[dict], turns: list[dict]) -> list[dict]:
    by_t = {t["t"]: t for t in turns}
    items = []
    for s in sig:
        if s["weight"] <= 0:
            continue
        user_turns = [n for n in s["turns"] if by_t.get(n, {}).get("role") == "USER"]
        quote = by_t[user_turns[-1]]["text"] if user_turns else ""
        items.append({"type": s["type"], "bucket": s.get("bucket"), "owner": s.get("owner"),
                      "severity": s["severity"], "turns": s["turns"], "user_quote": quote,
                      "what_went_wrong": s["detail"], "why_friction": WHY.get(s["type"], ""),
                      "expected_interpretation": "", "guidance": "", "confidence": None, "source": "rules"})
    return items


def run_day(day: date, use_llm: bool | None = None, triggered_by: str = "cli", run_id: int | None = None,
            session_ids: list[str] | None = None) -> dict:
    cfg = S.config()
    S.reload_config()  # pick up edits to config/friction.json
    cfg = S.config()
    mode = _mode(use_llm)
    if run_id is None:
        run_id = _create_run(day, use_llm, triggered_by)
    try:
        bundles = load_day(day, exclude_internal=cfg.get("exclude_internal_users", True), session_ids=session_ids)
        with conn() as c:
            reviewed = {r[0] for r in c.execute("SELECT DISTINCT session_id FROM items WHERE verdict <> 'pending'")}
        todo, skipped = [], 0
        for b in bundles:
            sid = b["facts"]["session_id"]
            if sid in reviewed:
                skipped += 1
                continue
            b["signals"] = S.compute(b)
            b["score"] = S.score(b["signals"])
            todo.append(b)
        _progress(run_id, f"{len(bundles)} chats loaded", sessions_scanned=len(bundles), sessions_skipped_reviewed=skipped)

        th = cfg["thresholds"]
        results: dict[str, dict] = {}   # session_id -> {summary, items, source}
        usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        judged = 0
        if mode == "llm":
            cands = [b for b in todo if b["score"] >= th["judge_min_score"]]
            cands.sort(key=lambda b: -b["score"])
            cands = cands[: cfg["llm"]["max_sessions_per_run"]]
            with conn() as c:
                fps = [dict(r) for r in c.execute(
                    "SELECT type, what_went_wrong, review_note FROM items WHERE verdict = 'not_friction' "
                    "ORDER BY reviewed_at DESC NULLS LAST LIMIT ?", (cfg["llm"]["max_known_false_positives_in_prompt"],))]
            system = J.build_system(fps)
            client = J.make_client()
            done = 0
            workers = cfg["llm"].get("subscription_concurrency", 2) if J.provider() == "subscription" else cfg["llm"]["concurrency"]
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(J.judge, client, system, render_for_llm(b["turns"]),
                                  [s for s in b["signals"]], b["facts"]): b for b in cands}
                for f in as_completed(futs):
                    b = futs[f]
                    sid = b["facts"]["session_id"]
                    done += 1
                    try:
                        out = f.result()
                        for k in usage:
                            usage[k] += out["_usage"][k]
                        judged += 1
                        if out["has_friction"] and out["items"]:
                            items = [{**i, "source": "llm"} for i in out["items"]]
                            results[sid] = {"summary": out["summary"], "items": items, "source": "llm"}
                    except Exception as e:  # one bad call should not kill the run
                        traceback.print_exc()
                        if b["score"] >= th["flag_min_score"]:
                            results[sid] = {"summary": f"LLM review failed ({type(e).__name__}: {str(e)[:160]}); showing rule signals.",
                                            "items": _rules_items(b["signals"], b["turns"]), "source": "rules"}
                    _progress(run_id, f"Judged {done}/{len(cands)} chats", sessions_judged=judged,
                              cost_usd=round(usage["cost_usd"], 4))
        else:
            for b in todo:
                if b["score"] >= th["flag_min_score"]:
                    items = _rules_items(b["signals"], b["turns"])
                    if items:
                        counts: dict[str, int] = {}
                        for i in items:
                            k = f"{TX.BUCKET_LABEL.get(i.get('bucket'), '?')}: {i['type'].replace('_', ' ')}"
                            counts[k] = counts.get(k, 0) + 1
                        results[b["facts"]["session_id"]] = {
                            "summary": "Rule signals · " + ", ".join(f"{k} ×{v}" for k, v in counts.items()),
                            "items": items, "source": "rules"}

        n_items = 0
        with conn() as c:
            analysed = [b["facts"]["session_id"] for b in todo]
            for i in range(0, len(analysed), 400):
                chunk = analysed[i:i + 400]
                q = ",".join("?" * len(chunk))
                old = [r[0] for r in c.execute(f"SELECT id FROM findings WHERE session_id IN ({q})", chunk)]
                if old:
                    oq = ",".join("?" * len(old))
                    c.execute(f"DELETE FROM items WHERE finding_id IN ({oq})", old)
                    c.execute(f"DELETE FROM findings WHERE id IN ({oq})", old)
            for b in todo:
                f = b["facts"]
                sid = f["session_id"]
                if sid not in results:
                    continue
                r = results[sid]
                cur = c.execute(
                    """INSERT INTO findings (run_id, day, session_id, user_id, title, flow, session_started_local, n_user_msgs,
                       outcome, score, source, summary, signals, transcript, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
                    (run_id, day.isoformat(), sid, f["user_id"], f["title"], f["current_flow"], f["created_local"],
                     f["n_user_msgs"], f["stage"], b["score"], r["source"], r["summary"],
                     json.dumps(b["signals"], default=str), json.dumps(b["turns"], default=str), now_iso()))
                fid = cur.fetchone()[0]
                for it in r["items"]:
                    it = _route(it, b)
                    c.execute(
                        """INSERT INTO items (finding_id, session_id, type, severity, turns, user_quote, what_went_wrong,
                           why_friction, expected_interpretation, guidance, confidence, source,
                           bucket, owner, fix_lane, served_model, lesson_is_rule)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (fid, sid, it["type"], it["severity"], json.dumps(it.get("turns") or []), it.get("user_quote"),
                         it.get("what_went_wrong"), it.get("why_friction"), it.get("expected_interpretation"),
                         it.get("guidance"), it.get("confidence"), it.get("source"),
                         it["bucket"], it["owner"], it["fix_lane"], it["served_model"], int(bool(it.get("lesson_is_rule")))))
                    n_items += 1
            c.execute("""UPDATE runs SET status='done', finished_at=?, sessions_flagged=?, items=?, sessions_judged=?,
                         input_tokens=?, output_tokens=?, cost_usd=?, progress=? WHERE id=?""",
                      (now_iso(), len(results), n_items, judged, usage["input_tokens"], usage["output_tokens"],
                       round(usage["cost_usd"], 4), "Done", run_id))
        return {"run_id": run_id, "mode": mode, "scanned": len(bundles), "skipped_reviewed": skipped,
                "flagged": len(results), "items": n_items, "cost_usd": round(usage["cost_usd"], 4)}
    except Exception as e:
        traceback.print_exc()
        with conn() as c:
            c.execute("UPDATE runs SET status='failed', finished_at=?, error=?, progress=? WHERE id=?",
                      (now_iso(), f"{type(e).__name__}: {e}", "Failed", run_id))
        raise
