"""Issue register, priorities and analyst briefs (Friction Finder, phase 3c).

Judged friction items are grouped into named root-cause *issues* ("Multi-route check blocks single-route
answers"), so the team reads a short ranked list per day / week / month instead of reviewing every chat.

- assign_pending(): Claude groups new LLM-judged items into existing issues or opens new ones.
- period_report(): counts, trend vs previous period, booking impact and P1/P2/P3 priority per issue.
- generate_brief(): Claude writes a consultant-style summary of a period from those numbers.
- write_rag_pack(): intent-capture RAG file built from the register (one lesson + best examples per issue).

Counts are exact: every item points at one issue, and stats are computed in SQL, not by the model.
"""
from __future__ import annotations

import json
import threading
import traceback
from datetime import date, datetime, timedelta, timezone

from ..config import settings
from ..db import ANALYTICS_SCHEMA
from . import judge as J
from . import taxonomy as TX
from .store import all_dicts, as_dict, conn, now_iso, save_export_file

BATCH = 30
MAX_ATTEMPTS = 3
SEV_W = {"high": 3, "medium": 2, "low": 1}
CLOSED = ("fixed", "wont_fix", "not_issue")
STATUSES = ("open", "in_progress", "fixed", "wont_fix", "not_issue")
INTENT_LLM_OWNERS = {"unified_intent_extraction", "unified_chat_route", "hotel_intent_extraction",
                     "multi_city_intent", "multi_route_check", "soft_signals"}

THEMES = ["Dates & trip type", "Cities & airports", "Passengers & fares", "Multi-route & multi-city",
          "Conversation flow", "Search results & options", "Answers & advice", "Hotels", "Speed",
          "Payments & booking", "Providers & availability", "App errors & delivery", "Missing feature"]

_state = {"running": False, "progress": None, "error": None, "last_done": None}
_lock = threading.Lock()


def state() -> dict:
    with conn() as c:
        pending = c.execute(f"""SELECT count(*) FROM items WHERE source = 'llm' AND issue_id IS NULL
                                AND verdict <> 'not_friction' AND issue_attempts < {MAX_ATTEMPTS}""").fetchone()[0]
    return {**_state, "unassigned": pending}


# ---------------------------------------------------------------------------------------------------------------
# 1. Grouping items into issues
# ---------------------------------------------------------------------------------------------------------------

def _catalog(c) -> list[dict]:
    return [dict(r) for r in c.execute("""
        SELECT s.id, s.bucket, s.owner, s.title, s.description, s.status, count(i.id) AS n
        FROM issues s LEFT JOIN items i ON i.issue_id = s.id
        WHERE s.merged_into IS NULL GROUP BY s.id ORDER BY n DESC""")]


def _prompt(catalog: list[dict], items: list[dict]) -> str:
    owners = ", ".join(sorted(TX.OWNER_KIND))
    cat = "\n".join(f"#{x['id']} [{x['bucket']}/{x['owner']}] {x['title']} — {(x['description'] or '')[:160]} ({x['n']} items)"
                    for x in catalog) or "(empty: no issues yet)"
    its = "\n".join(
        f"item {i['id']} [{i['bucket']}/{i['owner']}/{i['type']}/{i['severity']}] flow={i['flow'] or '-'}\n"
        f"  wrong: {(i['what_went_wrong'] or '')[:420]}\n"
        f"  user said: {(i['user_quote'] or '')[:160]}\n"
        f"  lesson: {(i['guidance'] or '')[:200]}"
        for i in items)
    return f"""You maintain the issue register for Bookkr, an AI travel-booking chat app (flights, hotels, trip plans) in India.
Each friction item below was found by a reviewer model in one real user chat. Group the items into ISSUES: one issue is
one root cause in Bookkr that an engineer, prompt owner or product manager could fix once, and that would then stop
recurring for every user who hits it.

How to group:
- Reuse an existing issue (by its #id) whenever the item has the same root cause, even if the city, date or wording differs.
- Open a new issue only for a root cause that no existing issue covers. Several items in this batch may share one new issue.
- Group by cause, not by symptom: "extractor reads 'single trip' as one-way" and "extractor drops the return date of a
  'from X to Y' range" are different issues; two chats that both hit the reversed-route bug are the same issue.
- Don't make issues so broad they say nothing ("intent misunderstood") or so narrow they name one chat ("Goa typo on 19 Nov").
  A good title names the component and the defect, e.g. "Multi-route check flags an airport choice ('KIX or TYO') as a multi-route request".
- Every item must be assigned exactly once.

For each NEW issue give:
- title: max 110 characters, plain English, the defect (not the fix).
- theme: the product area, one of: {", ".join(THEMES)}.
- description: 1-2 sentences: what goes wrong, when it happens, what the user experiences.
- bucket: technical | intent | suggestion.
- owner: the component that makes the first wrong decision, one of: {owners}.
- recommended_fix: one concrete next step for the owning team (code change, prompt rule, provider follow-up, product decision).
- lesson: ONLY if the owner is an LLM task (prompt-fixable): one general rule to add to that prompt, written as an instruction
  ("When the user answers an airport question with two airports, treat it as one route with alternatives, not a multi-route request.").
  Otherwise "".

Existing issues:
{cat}

New items:
{its}

Reply with ONLY a JSON object:
{{"new_issues": [{{"key": "n1", "title": "...", "theme": "...", "description": "...", "bucket": "...", "owner": "...", "recommended_fix": "...", "lesson": "..."}}],
  "assignments": [{{"item": 123, "issue": 45}}, {{"item": 124, "issue": "n1"}}]}}"""


def _assign_batch(items: list[dict]) -> int:
    with conn() as c:
        catalog = _catalog(c)
    out = J.ask_json(_prompt(catalog, items))
    valid_ids = {x["id"] for x in catalog}
    by_item = {i["id"]: i for i in items}
    now = now_iso()
    done = 0
    with conn() as c:
        keymap: dict[str, int] = {}
        for n in out.get("new_issues") or []:
            if not isinstance(n, dict) or not n.get("title"):
                continue
            bucket = n.get("bucket") if n.get("bucket") in TX.BUCKETS else None
            owner = TX.normalize_owner(n.get("owner")) if n.get("owner") in TX.OWNER_KIND else None
            theme = n.get("theme") if n.get("theme") in THEMES else None
            cur = c.execute("""INSERT INTO issues (title, theme, description, bucket, owner, recommended_fix, lesson, created_at, updated_at)
                               VALUES (?,?,?,?,?,?,?,?,?) RETURNING id""",
                            (str(n["title"])[:200], theme, str(n.get("description") or ""), bucket, owner,
                             str(n.get("recommended_fix") or ""), str(n.get("lesson") or ""), now, now))
            keymap[str(n.get("key"))] = cur.fetchone()[0]
        touched = set(keymap.values())
        for a in out.get("assignments") or []:
            if not isinstance(a, dict):
                continue
            try:
                iid = int(a.get("item"))
            except (TypeError, ValueError):
                continue
            target = a.get("issue")
            sid = keymap.get(str(target))
            if sid is None:
                try:
                    sid = int(str(target).lstrip("#"))
                except (TypeError, ValueError):
                    sid = None
                if sid not in valid_ids:
                    sid = None
            if iid in by_item and sid is not None:
                c.execute("UPDATE items SET issue_id = ? WHERE id = ? AND issue_id IS NULL", (sid, iid))
                touched.add(sid)
                done += 1
        ids = [i["id"] for i in items]
        c.execute(f"UPDATE items SET issue_attempts = issue_attempts + 1 WHERE issue_id IS NULL AND id IN ({','.join('?' * len(ids))})", ids)
        for sid in touched:  # fill bucket/owner from the items when the model left them out; recompute the lane
            s = c.execute("SELECT bucket, owner FROM issues WHERE id = ?", (sid,)).fetchone()
            maj = c.execute("""SELECT bucket, owner, count(*) n FROM items WHERE issue_id = ?
                               GROUP BY 1, 2 ORDER BY n DESC LIMIT 1""", (sid,)).fetchone()
            b = s["bucket"] or (maj["bucket"] if maj else "intent")
            o = s["owner"] or (maj["owner"] if maj else "unknown")
            c.execute("UPDATE issues SET bucket = ?, owner = ?, fix_lane = ?, updated_at = ? WHERE id = ?",
                      (b, o, TX.fix_lane(b, o), now, sid))
    return done


def _fill_themes() -> None:
    with conn() as c:
        todo = [dict(r) for r in c.execute("SELECT id, title, bucket, owner FROM issues WHERE theme IS NULL LIMIT 120")]
    if not todo:
        return
    lst = "\n".join(f"#{t['id']} [{t['bucket']}/{t['owner']}] {t['title']}" for t in todo)
    out = J.ask_json(f"""Put each Bookkr issue (AI travel-booking chat app) into one product area.
Areas: {", ".join(THEMES)}.
Issues:
{lst}
Reply with ONLY a JSON object: {{"themes": {{"<issue id>": "<area>"}}}}""")
    with conn() as c:
        for k, v in (out.get("themes") or {}).items():
            if v in THEMES and str(k).lstrip("#").isdigit():
                c.execute("UPDATE issues SET theme = ? WHERE id = ? AND theme IS NULL", (v, int(str(k).lstrip("#"))))


def assign_pending(max_batches: int | None = None) -> dict:
    """Group every unassigned LLM-judged item into an issue. Sequential, because each batch can open issues
    the next batch should reuse."""
    if not J.llm_enabled():
        return {"assigned": 0, "skipped": "LLM is off"}
    if not _lock.acquire(blocking=False):
        return {"assigned": 0, "skipped": "already running"}
    _state.update(running=True, error=None, progress="starting")
    total, batches = 0, 0
    try:
        while max_batches is None or batches < max_batches:
            with conn() as c:
                items = all_dicts(c.execute(f"""
                    SELECT i.id, i.bucket, i.owner, i.type, i.severity, i.what_went_wrong, i.user_quote, i.guidance, f.flow
                    FROM items i JOIN findings f ON f.id = i.finding_id
                    WHERE i.source = 'llm' AND i.issue_id IS NULL AND i.verdict <> 'not_friction'
                      AND i.issue_attempts < {MAX_ATTEMPTS}
                    ORDER BY i.bucket NULLS FIRST, i.owner NULLS FIRST, i.type, i.id LIMIT {BATCH}""").fetchall())
                left = c.execute(f"""SELECT count(*) FROM items WHERE source = 'llm' AND issue_id IS NULL
                                     AND verdict <> 'not_friction' AND issue_attempts < {MAX_ATTEMPTS}""").fetchone()[0]
            if not items:
                break
            _state["progress"] = f"grouping items: {left} left"
            try:
                total += _assign_batch(items)
            except Exception as e:  # noqa: BLE001  one bad batch must not stop the rest
                traceback.print_exc()
                _state["error"] = str(e)[:300]
                with conn() as c:
                    ids = [i["id"] for i in items]
                    c.execute(f"UPDATE items SET issue_attempts = issue_attempts + 1 WHERE id IN ({','.join('?' * len(ids))})", ids)
            batches += 1
        try:
            _fill_themes()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    finally:
        _state.update(running=False, progress=None, last_done=now_iso())
        _lock.release()
    return {"assigned": total, "batches": batches}


def assign_in_background() -> bool:
    if _state["running"]:
        return False
    threading.Thread(target=assign_pending, daemon=True).start()
    return True


# ---------------------------------------------------------------------------------------------------------------
# 2. Periods and the priority report
# ---------------------------------------------------------------------------------------------------------------

def bounds(period: str, anchor: date) -> tuple[date, date]:
    if period == "day":
        return anchor, anchor
    if period == "last7":                                # rolling 7 days ending on the anchor day
        return anchor - timedelta(days=6), anchor
    if period == "week":
        s = anchor - timedelta(days=anchor.weekday())
        return s, s + timedelta(days=6)
    if period == "month":
        s = anchor.replace(day=1)
        nxt = (s.replace(day=28) + timedelta(days=4)).replace(day=1)
        return s, nxt - timedelta(days=1)
    raise ValueError("period must be day, last7, week or month")


def previous(period: str, start: date) -> tuple[date, date]:
    return bounds(period, start - timedelta(days=1))


def anchor_for(period: str, start: date) -> date:
    """The anchor that gives a period starting on `start` (last7 is anchored on its end day)."""
    return start + timedelta(days=6) if period == "last7" else start


def periods() -> dict:
    """Periods that have friction data, newest first, with how much of each was judged by the LLM."""
    with conn() as c:
        days = {r["day"]: dict(r) for r in c.execute("""
            SELECT r.day, r.mode, r.sessions_scanned FROM runs r
            WHERE r.id IN (SELECT max(id) FROM runs WHERE status = 'done' GROUP BY day)""")}
    out = {"day": [], "last7": [], "week": [], "month": []}
    if not days:
        return out
    last = max(date.fromisoformat(d) for d in days)
    s7, e7 = bounds("last7", last)
    in7 = [d for d in days if s7.isoformat() <= d <= e7.isoformat()]
    out["last7"] = [{"start": s7.isoformat(), "end": e7.isoformat(), "days": len(in7),
                     "judged_days": sum(days[d]["mode"] == "llm" for d in in7), "complete": True}]
    for p in ("day", "week", "month"):
        seen = {}
        for d in sorted(days, reverse=True):
            s, e = bounds(p, date.fromisoformat(d))
            k = s.isoformat()
            x = seen.setdefault(k, {"start": k, "end": e.isoformat(), "days": 0, "judged_days": 0,
                                    "complete": e <= last})
            x["days"] += 1
            x["judged_days"] += days[d]["mode"] == "llm"
        out[p] = list(seen.values())
    return out


def _baseline(start: date, end: date, journey: str | None = None) -> dict:
    """All chats in the period (same day rule as the finder) and how many booked: the yardstick for impact."""
    try:
        from ..db import connect, rows
        with connect() as con:
            r = rows(con, f"""
                WITH s AS (SELECT m.chat_session_id, CAST(max(m.created_local) AS DATE) AS day
                           FROM messages m JOIN session_facts sf ON sf.session_id = m.chat_session_id
                           WHERE m.role = 'USER' AND NOT sf.is_internal GROUP BY 1)
                SELECT count(*) AS chats, count(DISTINCT sf.user_id) AS users,
                       count(*) FILTER (WHERE sf.n_booked > 0) AS booked
                FROM s JOIN session_facts sf ON sf.session_id = s.chat_session_id
                WHERE s.day BETWEEN $s AND $e {'AND sf.journey_type = $j' if journey else ''}""",
                     {"s": start, "e": end, "j": journey})
        b = r[0] if r else {}
    except Exception:  # noqa: BLE001
        b = {}
    chats = int(b.get("chats") or 0)
    return {"chats": chats, "users": int(b.get("users") or 0), "booked": int(b.get("booked") or 0),
            "booking_rate_pct": round(100 * (b.get("booked") or 0) / chats, 1) if chats else None}


ISSUE_STATS = """
    SELECT i.issue_id AS id, count(*) AS items, count(DISTINCT i.session_id) AS chats,
           count(DISTINCT coalesce(f.user_id, i.session_id)) AS users,
           avg(CASE i.severity WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) AS sev,
           sum((i.severity = 'high')::int) AS high,
           count(DISTINCT CASE WHEN f.outcome = 'Booked' THEN i.session_id END) AS booked_chats,
           min(f.day) AS first_day, max(f.day) AS last_day
    FROM items i JOIN findings f ON f.id = i.finding_id
    WHERE i.issue_id IS NOT NULL AND i.verdict <> 'not_friction' AND f.day BETWEEN ? AND ? {jf}
    GROUP BY 1"""

JOURNEYS = ("one_way", "round_trip", "flight_hotel", "hotel", "trip_plan", "unclear")


def journey_filter(journey: str | None, alias: str = "i") -> tuple[str, list]:
    """SQL (and its parameter) keeping only items from chats of one journey type. The journey lives in the analytics
    copy (analyst.session_facts), in the same database as the friction schema."""
    if not journey:
        return "", []
    if journey not in JOURNEYS:
        raise ValueError(f"journey must be one of {', '.join(JOURNEYS)}")
    return (f"AND {alias}.session_id IN (SELECT session_id FROM {ANALYTICS_SCHEMA}.session_facts WHERE journey_type = ?)",
            [journey])


def period_report(period: str, anchor: date, journey: str | None = None) -> dict:
    start, end = bounds(period, anchor)
    pstart, pend = previous(period, start)
    s, e, ps, pe = (x.isoformat() for x in (start, end, pstart, pend))
    jf, jp = journey_filter(journey)
    with conn() as c:
        cur = {r["id"]: dict(r) for r in c.execute(ISSUE_STATS.format(jf=jf), (s, e, *jp))}
        prev = {r["id"]: dict(r) for r in c.execute(ISSUE_STATS.format(jf=jf), (ps, pe, *jp))}
        ids = set(cur) | set(prev)
        meta = {r["id"]: dict(r) for r in c.execute(
            f"SELECT * FROM issues WHERE id IN ({','.join('?' * len(ids))})", list(ids))} if ids else {}
        daily = [dict(r) for r in c.execute(f"""
            SELECT f.day, i.issue_id, count(DISTINCT i.session_id) AS chats
            FROM items i JOIN findings f ON f.id = i.finding_id
            WHERE i.issue_id IS NOT NULL AND i.verdict <> 'not_friction' AND f.day BETWEEN ? AND ? {jf} GROUP BY 1, 2""",
            (s, e, *jp))]
        runs = [dict(r) for r in c.execute("""
            SELECT day, mode, sessions_scanned, sessions_judged, sessions_flagged FROM runs
            WHERE id IN (SELECT max(id) FROM runs WHERE status = 'done' GROUP BY day) AND day BETWEEN ? AND ?""", (s, e))]
        # every item (rules + LLM) by bucket and type: the "what kind of friction" view
        types = [dict(r) for r in c.execute(f"""
            SELECT i.bucket, i.type, i.source, count(*) AS items, count(DISTINCT i.session_id) AS chats
            FROM items i JOIN findings f ON f.id = i.finding_id
            WHERE i.verdict <> 'not_friction' AND f.day BETWEEN ? AND ? {jf} GROUP BY 1, 2, 3""", (s, e, *jp))]
        prev_types = {(r["bucket"], r["type"]): r["chats"] for r in c.execute(f"""
            SELECT i.bucket, i.type, count(DISTINCT i.session_id) AS chats
            FROM items i JOIN findings f ON f.id = i.finding_id
            WHERE i.verdict <> 'not_friction' AND f.day BETWEEN ? AND ? {jf} GROUP BY 1, 2""", (ps, pe, *jp))}
        unassigned = c.execute(f"""SELECT count(*) FROM items i JOIN findings f ON f.id = i.finding_id
                                  WHERE i.source = 'llm' AND i.issue_id IS NULL AND i.verdict <> 'not_friction'
                                    AND f.day BETWEEN ? AND ? {jf}""", (s, e, *jp)).fetchone()[0]
    base = _baseline(start, end, journey)
    judged_chats = sum(r["sessions_scanned"] or 0 for r in runs if r["mode"] == "llm")   # every chat on judged days
    read_by_judge = sum(r["sessions_judged"] or 0 for r in runs if r["mode"] == "llm")   # had a signal, so the judge read it

    issues = []
    for iid, m in meta.items():
        x = cur.get(iid)
        p = prev.get(iid)
        if not x and not p:
            continue
        x = x or {"items": 0, "chats": 0, "users": 0, "sev": 0, "high": 0, "booked_chats": 0, "first_day": None, "last_day": None}
        regression = m["status"] == "fixed" and m["status_at"] and x["last_day"] and x["last_day"] >= m["status_at"][:10]
        score = round(x["users"] * (x["sev"] or 0), 1)
        issues.append({
            "id": iid, "title": m["title"], "theme": m.get("theme") or "Other", "description": m["description"],
            "bucket": m["bucket"], "owner": m["owner"],
            "owner_label": TX.OWNER_LABEL.get(m["owner"], m["owner"]), "fix_lane": m["fix_lane"],
            "lane_label": TX.FIX_LANE_LABEL.get(m["fix_lane"], m["fix_lane"]),
            "recommended_fix": m["recommended_fix"], "lesson": m["lesson"], "status": m["status"],
            "status_note": m["status_note"], "status_at": m["status_at"],
            "chats": x["chats"], "users": x["users"], "items": x["items"], "high": x["high"],
            "avg_severity": round(x["sev"] or 0, 2), "booked_chats": x["booked_chats"],
            "prev_chats": (p or {}).get("chats", 0), "first_day": x["first_day"], "last_day": x["last_day"],
            "share_of_judged_pct": round(100 * x["chats"] / judged_chats, 1) if judged_chats else None,
            "score": score, "regression": bool(regression),
            "new_this_period": bool(m["created_at"][:10] >= s),
        })
    active = sorted((i for i in issues if i["chats"] and (i["status"] not in CLOSED or i["regression"])),
                    key=lambda i: (-i["score"], -i["chats"], i["id"]))
    for rank, i in enumerate(active, 1):
        i["rank"] = rank
        i["priority"] = "P1" if rank <= 3 else "P2" if rank <= 10 else "P3"
        d = i["chats"] - i["prev_chats"]
        i["trend"] = "new" if not i["prev_chats"] else "up" if d > 0 else "down" if d < 0 else "flat"
    gone = sorted((i for i in issues if not i["chats"] and i["prev_chats"] and i["status"] not in ("not_issue",)),
                  key=lambda i: -i["prev_chats"])
    closed = [i for i in issues if i["chats"] and i["status"] in CLOSED and not i["regression"]]

    by_bucket = {b: {"bucket": b, "label": TX.BUCKET_LABEL[b], "chats": 0, "issues": 0} for b in TX.BUCKETS}
    for i in active:
        if i["bucket"] in by_bucket:
            by_bucket[i["bucket"]]["issues"] += 1
    with conn() as c:
        for r in c.execute(f"""SELECT i.bucket, count(DISTINCT i.session_id) FROM items i JOIN findings f ON f.id = i.finding_id
                              WHERE i.issue_id IS NOT NULL AND i.verdict <> 'not_friction' AND f.day BETWEEN ? AND ? {jf}
                              GROUP BY 1""", (s, e, *jp)):
            if r[0] in by_bucket:
                by_bucket[r[0]]["chats"] = r[1]
    themes: dict[str, dict] = {}
    for i in active:
        t = themes.setdefault(i["theme"], {"theme": i["theme"], "issues": 0, "chats": 0, "prev_chats": 0, "score": 0.0,
                                           "top_issue": i["title"], "top_issue_id": i["id"]})
        t["issues"] += 1
        t["chats"] += i["chats"]   # an issue-chat pair; a chat hitting two issues of one theme counts twice
        t["prev_chats"] += i["prev_chats"]
        t["score"] = round(t["score"] + i["score"], 1)
    tmap: dict[tuple, dict] = {}
    for r in types:
        k = (r["bucket"], r["type"])
        t = tmap.setdefault(k, {"bucket": r["bucket"], "type": r["type"], "description": TX.TYPES.get(r["type"], ("", r["type"]))[1],
                                "chats": 0, "llm_chats": 0, "rules_chats": 0})
        t["chats"] += r["chats"]
        t[f"{r['source']}_chats"] = t.get(f"{r['source']}_chats", 0) + r["chats"]
    for k, t in tmap.items():
        t["prev_chats"] = prev_types.get(k, 0)
    return {
        "period": period, "start": s, "end": e, "prev_start": ps, "prev_end": pe,
        "baseline": base, "judged_chats": judged_chats, "read_by_judge": read_by_judge,
        "coverage": {"days": len(runs), "judged_days": sum(r["mode"] == "llm" for r in runs),
                     "rules_only_days": sorted(r["day"] for r in runs if r["mode"] != "llm"),
                     "unassigned_items": unassigned},
        "issues": active, "closed_but_seen": closed, "gone_quiet": gone[:15],
        "by_bucket": list(by_bucket.values()),
        "by_theme": sorted(themes.values(), key=lambda t: -t["score"]),
        "by_type": sorted(tmap.values(), key=lambda t: -t["chats"]),
        "daily": daily,
    }


def issue_detail(iid: int) -> dict | None:
    with conn() as c:
        s = as_dict(c.execute("SELECT * FROM issues WHERE id = ?", (iid,)).fetchone())
        if not s:
            return None
        s["items"] = all_dicts(c.execute("""
            SELECT i.id, i.finding_id, i.session_id, i.type, i.severity, i.what_went_wrong, i.user_quote,
                   i.expected_interpretation, i.guidance, i.confidence, i.verdict, i.served_model,
                   f.day, f.flow, f.outcome, f.title AS chat_title
            FROM items i JOIN findings f ON f.id = i.finding_id
            WHERE i.issue_id = ? AND i.verdict <> 'not_friction' ORDER BY f.day DESC, i.id DESC""", (iid,)).fetchall())
        s["daily"] = [dict(r) for r in c.execute("""
            SELECT f.day, count(DISTINCT i.session_id) AS chats FROM items i JOIN findings f ON f.id = i.finding_id
            WHERE i.issue_id = ? AND i.verdict <> 'not_friction' GROUP BY 1 ORDER BY 1""", (iid,))]
    s["owner_label"] = TX.OWNER_LABEL.get(s["owner"], s["owner"])
    s["lane_label"] = TX.FIX_LANE_LABEL.get(s["fix_lane"], s["fix_lane"])
    s["chats"] = len({i["session_id"] for i in s["items"]})
    return s


def update_issue(iid: int, fields: dict, by: str | None) -> dict | None:
    allowed = {k: v for k, v in fields.items() if k in ("title", "description", "recommended_fix", "lesson", "status",
                                                         "status_note", "owner", "bucket") and v is not None}
    if "status" in allowed and allowed["status"] not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if "owner" in allowed and allowed["owner"] not in TX.OWNER_KIND:
        raise ValueError("unknown owner")
    if "bucket" in allowed and allowed["bucket"] not in TX.BUCKETS:
        raise ValueError("unknown bucket")
    with conn() as c:
        old = c.execute("SELECT * FROM issues WHERE id = ?", (iid,)).fetchone()
        if not old:
            return None
        now = now_iso()
        if "status" in allowed and allowed["status"] != old["status"]:
            allowed["status_at"] = now
            allowed["status_by"] = by
        if "owner" in allowed or "bucket" in allowed:
            allowed["fix_lane"] = TX.fix_lane(allowed.get("bucket", old["bucket"]), allowed.get("owner", old["owner"]))
        allowed["updated_at"] = now
        c.execute(f"UPDATE issues SET {', '.join(f'{k} = ?' for k in allowed)} WHERE id = ?", [*allowed.values(), iid])
    return issue_detail(iid)


def merge_issues(keep: int, drop: int) -> dict | None:
    with conn() as c:
        c.execute("UPDATE items SET issue_id = ? WHERE issue_id = ?", (keep, drop))
        c.execute("UPDATE issues SET merged_into = ?, status = 'not_issue', updated_at = ? WHERE id = ?", (keep, now_iso(), drop))
    return issue_detail(keep)


# ---------------------------------------------------------------------------------------------------------------
# 3. Analyst brief
# ---------------------------------------------------------------------------------------------------------------

def _brief_input(rep: dict) -> dict:
    with conn() as c:
        def samples(iid):
            return [r[0][:260] for r in c.execute("""SELECT what_went_wrong FROM items WHERE issue_id = ? AND verdict <> 'not_friction'
                                                     ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id DESC
                                                     LIMIT 2""", (iid,))]
        top = [{k: i[k] for k in ("id", "rank", "priority", "title", "bucket", "owner_label", "lane_label", "chats", "users",
                                  "prev_chats", "trend", "avg_severity", "booked_chats", "status", "regression",
                                  "new_this_period", "recommended_fix")} | {"examples": samples(i["id"])}
               for i in rep["issues"][:15]]
    return {"period": rep["period"], "start": rep["start"], "end": rep["end"],
            "previous_period": [rep["prev_start"], rep["prev_end"]],
            "all_chats_in_period": rep["baseline"], "chats_read_by_judge": rep["judged_chats"],
            "coverage": rep["coverage"], "friction_by_bucket": rep["by_bucket"],
            "friction_by_type_top": rep["by_type"][:12], "top_issues": top,
            "issues_gone_quiet": [{"id": i["id"], "title": i["title"], "prev_chats": i["prev_chats"]} for i in rep["gone_quiet"][:6]],
            "marked_fixed_but_seen_again": [i["title"] for i in rep["issues"] if i["regression"]]}


def _brief_prompt(data: dict) -> str:
    return f"""You are the business analyst for Bookkr, an AI travel-booking chat app in India (flights, hotels, trip plans).
Each period, an automated Friction Finder reads user chats, finds where users struggled, and groups those moments into
named issues with exact counts. Write the {({"day": "daily", "last7": "last-7-days", "week": "weekly", "month": "monthly"})[data['period']]} brief for the founders and engineering leads: what hurt users
most, what to fix first and why, and what changed since the previous period.

Rules:
- Use ONLY the numbers in the data below. Don't invent counts, money, percentages or causes. Say "chats" and "users", not "items".
- Rank by user impact (users x severity, already in `rank`), but you may call out a lower-ranked issue as a quick win
  when its fix is small (a prompt rule, a config, a missing airport) or as urgent when money or bookings are involved.
- Every action must name the issue id, the team (Engineering, AI / prompts, Product, Ops) and one concrete next step.
- If coverage is partial (rules-only days, unassigned items), say so in data_note in one sentence.
- Plain, direct English. No jargon like "synergy". Short sentences.

Data:
{json.dumps(data, ensure_ascii=False, default=str)}

Reply with ONLY a JSON object:
{{"headline": "one sentence, max 140 chars: the single most important thing this period",
  "summary": "3-4 sentences for a founder: overall state, the biggest problem, the trend",
  "top_actions": [{{"issue_id": 1, "title": "short issue name", "why": "impact in numbers", "action": "concrete next step", "team": "Engineering", "effort": "small|medium|large"}}],
  "quick_wins": [{{"issue_id": 1, "note": "why it's cheap and worth doing now"}}],
  "worsening": [{{"issue_id": 1, "note": "what grew and by how much"}}],
  "improving": [{{"issue_id": 1, "note": "what dropped"}}],
  "watch": ["one-line risks or patterns to keep an eye on"],
  "data_note": "coverage caveat or empty string"}}
top_actions: 3 to 5 entries. The other lists: 0 to 4 entries each."""


def generate_brief(period: str, anchor: date, triggered_by: str = "ui") -> dict:
    rep = period_report(period, anchor)
    data = _brief_input(rep)
    if not rep["issues"]:
        content = {"headline": "No grouped issues in this period yet.", "summary": "", "top_actions": [], "quick_wins": [],
                   "worsening": [], "improving": [], "watch": [],
                   "data_note": "Run the LLM judge on these days, then group the items into issues."}
    else:
        content = J.ask_json(_brief_prompt(data))
    with conn() as c:
        cur = c.execute("""INSERT INTO briefs (period, start, "end", created_at, triggered_by, model, content, stats)
                           VALUES (?,?,?,?,?,?,?,?) RETURNING id""",
                        (period, rep["start"], rep["end"], now_iso(), triggered_by, J.model_name(),
                         json.dumps(content, ensure_ascii=False), json.dumps(data, ensure_ascii=False, default=str)))
        bid = cur.fetchone()[0]
    return latest_brief(period, rep["start"]) or {"id": bid}


def latest_brief(period: str, start: str) -> dict | None:
    with conn() as c:
        return as_dict(c.execute("""SELECT id, period, start, "end", created_at, triggered_by, model, content FROM briefs
                                    WHERE period = ? AND start = ? ORDER BY id DESC LIMIT 1""", (period, start)).fetchone())


# ---------------------------------------------------------------------------------------------------------------
# 4. Intent-capture RAG pack
# ---------------------------------------------------------------------------------------------------------------

def rag_pack_items(min_confidence: float = 0.7, per_issue: int = 3) -> list[dict]:
    """Best examples per intent issue an LLM task owns. Human-agreed items first, then high-confidence judged items."""
    from .export import build_example
    with conn() as c:
        rows = all_dicts(c.execute(f"""
            SELECT i.*, f.day, f.flow, f.title, f.transcript, f.session_started_local, f.submitted_at,
                   s.title AS issue_title, s.lesson AS issue_lesson, s.status AS issue_status, s.owner AS issue_owner
            FROM items i JOIN findings f ON f.id = i.finding_id JOIN issues s ON s.id = i.issue_id
            WHERE i.verdict <> 'not_friction' AND s.merged_into IS NULL AND s.status <> 'not_issue'
              AND s.bucket = 'intent' AND s.owner IN ({','.join('?' * len(INTENT_LLM_OWNERS))})
              AND (i.verdict = 'agree' OR coalesce(i.confidence, 0) >= ?)
              AND coalesce(i.corrected_interpretation, i.expected_interpretation, '') <> ''""",
            [*sorted(INTENT_LLM_OWNERS), min_confidence]).fetchall())
    rows.sort(key=lambda r: (r["verdict"] != "agree", -SEV_W.get(r["severity"], 1), -(r["confidence"] or 0), -r["id"]))
    picked: dict[int, list] = {}
    for r in rows:
        lst = picked.setdefault(r["issue_id"], [])
        if len(lst) < per_issue and all((r["user_quote"] or "") != (x["user_quote"] or "") for x in lst):
            lst.append(r)
    out = []
    for iid, lst in picked.items():
        for r in lst:
            e = build_example(r)
            ctx = " ".join(f"{t['role']}: {t['text'][:200]}" for t in e["context"][-3:])
            e.update({
                "issue_id": iid, "issue_title": r["issue_title"], "lesson": r["issue_lesson"] or e["guidance"],
                "target_prompt": r["issue_owner"], "issue_status": r["issue_status"],
                "review_status": "human_agreed" if r["verdict"] == "agree" else "auto_high_confidence",
                "confidence": r["confidence"],
                # what to embed for retrieval: the user's words plus the turns just before them
                "retrieval_text": f"{e['user_message']} || {ctx}"[:1200],
            })
            out.append(e)
    return out


def write_rag_pack(min_confidence: float = 0.7, per_issue: int = 3) -> dict:
    exs = rag_pack_items(min_confidence, per_issue)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    jl = settings.exports_dir / f"bookkr_intent_rag_pack_{stamp}.jsonl"
    md = settings.exports_dir / f"bookkr_intent_rag_pack_{stamp}.md"
    with open(jl, "w", encoding="utf-8") as f:
        for e in exs:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    by_prompt: dict[str, dict[int, list]] = {}
    for e in exs:
        by_prompt.setdefault(e["target_prompt"], {}).setdefault(e["issue_id"], []).append(e)
    n_agreed = sum(e["review_status"] == "human_agreed" for e in exs)
    lines = ["# Bookkr intent capture: RAG pack",
             f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from production chats. {len(exs)} examples across "
             f"{sum(len(v) for v in by_prompt.values())} issues; {n_agreed} human-agreed, the rest judged with confidence ≥ {min_confidence}._",
             "",
             "How to use: put the **Rules** block of each prompt into that prompt's system text. Index the JSONL file's "
             "`retrieval_text` and, at run time, retrieve the 2-3 nearest examples for the user's message and add them as "
             "few-shot examples (user message → correct interpretation).", ""]
    for owner, issues in by_prompt.items():
        lines += [f"## {TX.OWNER_LABEL.get(owner, owner)} (`{owner}`)", "", "**Rules**"]
        for iid, lst in issues.items():
            if lst[0]["lesson"]:
                lines.append(f"- {lst[0]['lesson']}  _(issue #{iid}, {lst[0]['issue_title']})_")
        lines.append("")
        for iid, lst in issues.items():
            lines += [f"### Issue #{iid}: {lst[0]['issue_title']}", ""]
            for n, e in enumerate(lst, 1):
                exp = e["expected_interpretation"]
                exp = exp if isinstance(exp, str) else json.dumps(exp, ensure_ascii=False)
                lines += [f"**Example {n}** ({e['review_status'].replace('_', ' ')}, {e['flow'] or 'unknown flow'})",
                          f"- User said: \"{e['user_message']}\"" if e["user_message"] else "",
                          "- Conversation around it:",
                          *(f"> **{t['role']}:** {t['text'][:400]}" for t in e["context"][-4:]),
                          "",
                          f"- What Bookkr did wrong: {e['what_went_wrong']}",
                          f"- Correct interpretation: {exp}", ""]
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for f in (jl, md):
        save_export_file(f.name, f.read_text(encoding="utf-8"))
    counts = {"examples": len(exs), "issues": sum(len(v) for v in by_prompt.values()), "human_agreed": n_agreed}
    files = [{"group": "intent_rag_pack", "label": "Intent RAG pack", "count": len(exs), "jsonl": jl.name, "md": md.name}]
    with conn() as c:
        c.execute("""INSERT INTO exports (created_at, created_by, examples, rules, jsonl_file, md_file, files, counts)
                     VALUES (?,?,?,?,?,?,?,?)""",
                  (now_iso(), "auto (RAG pack)", len(exs), counts["issues"], jl.name, md.name, json.dumps(files), json.dumps(counts)))
    return {"files": files, **counts}


# ---------------------------------------------------------------------------------------------------------------
# 5. Automation hook (called by the daily scheduler after it judges yesterday)
# ---------------------------------------------------------------------------------------------------------------

def after_daily_run(day: date) -> None:
    assign_pending()
    generate_brief("day", day, "schedule")
    generate_brief("last7", day, "schedule")
    if day.weekday() == 6:                               # Sunday done -> weekly brief for Mon-Sun
        generate_brief("week", day, "schedule")
    if (day + timedelta(days=1)).day == 1:               # last day of the month -> monthly brief
        generate_brief("month", day, "schedule")
    write_rag_pack()
