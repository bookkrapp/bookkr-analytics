"""Load one day of chats from the analytics DB and turn each into a numbered transcript."""
from __future__ import annotations

import json
from datetime import date

from ..config import settings
from ..db import connect, rows

OFF = int(settings.tz_offset_minutes)


def _val(v) -> str | None:
    """Compact rendering of an intent slot value."""
    if v is None or v == "" or v == [] or v == {}:
        return None
    if isinstance(v, (str, int, float, bool)):
        return str(v)
    if isinstance(v, list):
        parts = [p for p in (_val(x) for x in v) if p]
        return ", ".join(parts) if parts else None
    if isinstance(v, dict):
        if v.get("name") or v.get("iata") or v.get("city"):
            name = v.get("name") or v.get("city")
            return " ".join(p for p in [name, f"({v['iata']})" if v.get("iata") else None] if p)
        for k in ("exact_date", "exactDate", "date"):
            if v.get(k):
                return str(v[k])
        s, e = v.get("start_date") or v.get("startDate"), v.get("end_date") or v.get("endDate")
        if s or e:
            return f"{s or '?'} – {e or '?'}"
        if v.get("month"):
            return str(v["month"])
        if "adults" in v:
            return f"{v.get('adults')} adults, {v.get('children') or 0} children, {v.get('infants') or 0} infants"
        inner = [f"{k}={x}" for k, x in ((k, _val(x)) for k, x in v.items()) if x]
        return "; ".join(inner) if inner else None
    return str(v)


def intent_summary(params) -> str:
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except ValueError:
            return params[:300]
    if not isinstance(params, dict):
        return ""
    parts = []
    for k, v in params.items():
        s = _val(v)
        if s:
            # fare category comes from the user's tap on the confirmation card, not from what they typed
            parts.append(f"{k}={s} (fare category the user picked on the card or typed)" if k == "passengerFareType"
                         else f"{k}={s}")
    return "; ".join(parts)[:900]


def _fare_picker(d: dict) -> str:
    """The special-fare picker on the confirmation card. A tap on it is not a chat message, so say it was offered."""
    sf = d.get("specialFares") if isinstance(d.get("specialFares"), dict) else None
    if not sf or sf.get("disabled"):
        return ""
    opts = " / ".join(o.get("label") or o.get("type") or "?" for o in sf.get("options") or [] if isinstance(o, dict))
    return f"; special-fare picker offered ({opts}), user's pick: {sf.get('selected') or 'none yet'}" if opts else ""


def card_summary(card_type, card_data, card_shape) -> str | None:
    if not card_type:
        return None
    d = card_data if isinstance(card_data, dict) else None
    if card_type == "intent_confirmation" and d and d.get("summary"):
        fields = "; ".join(f"{s.get('label')}={s.get('value')}" for s in d["summary"])
        return f"[intent confirmation card: {fields}; complete={d.get('isComplete')}; actions={d.get('actions')}{_fare_picker(d)}]"
    if card_type == "payment_result" and d:
        return f"[payment result card: outcome={d.get('outcome')}; amount={d.get('amount')}]"
    shape = card_shape if isinstance(card_shape, dict) else {}
    counts = ", ".join(f"{k}={v}" for k, v in shape.items() if isinstance(v, (int, float)))
    return f"[{card_type} card{': ' + counts if counts else ''}]"


def _parse(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _short_model(m: str | None) -> str:
    m = m or "?"
    if "nemotron" in m.lower():
        return "Nemotron LoRA"
    if "qwen" in m.lower() or "wandb" in m.lower():
        return "Qwen LoRA"
    return m.split("/")[-1][:40]


def analytics_text(e: dict) -> str | None:
    """One transcript line per analytics event that matters for friction (None = keep it out of the transcript)."""
    p = e.get("properties") if isinstance(e.get("properties"), dict) else {}
    n = e["event_name"]
    if n == "payment_failed":
        return f"PAYMENT FAILED ({p.get('failure_stage') or '?'}): {p.get('error_code') or ''} {p.get('reason') or ''}".strip()
    if n == "payment_dismissed":
        return "PAYMENT WINDOW CLOSED UNPAID" + (" after a failure" if p.get("after_failure") else "")
    if n == "booking_price_changed":
        return f"PRICE CHANGED at {p.get('source') or '?'}: {p.get('direction') or '?'} {p.get('change_bucket') or ''}".strip()
    if n == "booking_fare_expired":
        return f"FARE EXPIRED at {p.get('source') or '?'}"
    if n == "unsupported_feature_requested":
        return f"UNSUPPORTED FEATURE REQUESTED: {p.get('feature') or '?'}"
    if n in ("hotel_search_results_shown", "flight_search_results_shown"):
        rc = p.get("result_count")
        return f"{'HOTEL' if n.startswith('hotel') else 'FLIGHT'} RESULTS SHOWN: 0 results" if rc in (0, "0") else None
    if n == "feature_used":
        return f"USER USED {str(p.get('feature') or '').replace('_', ' ').upper()}: {p.get('action') or ''}" \
               + (f" ({p.get('filter_type') or p.get('sort_option')})" if p.get("filter_type") or p.get("sort_option") else "")
    return None


def load_day(day: date, exclude_internal: bool = True, session_ids: list[str] | None = None) -> list[dict]:
    """Every chat whose LAST user message falls on `day` (IST), with its full history.

    Owning a chat by its last active day means each chat is analysed once, with its whole conversation,
    even when it spans several days.
    """
    with connect() as con:
        if session_ids:
            ids = session_ids
        else:
            ids = [r[0] for r in con.execute(f"""
                SELECT m.chat_session_id FROM messages m
                JOIN session_facts sf ON sf.session_id = m.chat_session_id
                WHERE m.role = 'USER' AND (NOT $x OR NOT sf.is_internal)
                GROUP BY 1 HAVING CAST(max(m.created_local) AS DATE) = $d""", {"d": day, "x": exclude_internal}).fetchall()]
        if not ids:
            return []
        p = {"ids": ids}
        facts = rows(con, """SELECT sf.*, st.name AS stage FROM session_facts sf
                             JOIN stages st ON st.level = sf.stage_level
                             WHERE session_id = ANY($ids)""", p)
        msgs = rows(con, """SELECT id, chat_session_id, role, content, kind, card_type, card_data, card_shape,
                                   created_at, created_local
                            FROM messages WHERE chat_session_id = ANY($ids) ORDER BY created_at""", p,
                    json_cols=("card_data", "card_shape"))
        intents = rows(con, """SELECT chat_session_id, message_id, intent_type, confidence, parameters, created_at
                               FROM raw_intents WHERE deleted_at IS NULL AND chat_session_id = ANY($ids)
                               ORDER BY created_at""", p, json_cols=("parameters",))
        flights = rows(con, """SELECT f.chat_session_id, f.origin, f.destination, f.departure_date, f.return_date,
                                      f.adults, f.children, f.infants, f.cabin_class, f.search_hash, f.created_at,
                                      (SELECT coalesce(sum(option_count), 0) FROM raw_flight_search_results r
                                        WHERE r.search_request_id = f.id) AS options,
                                      (SELECT count(*) FROM raw_flight_search_results r
                                        WHERE r.search_request_id = f.id) AS result_sets
                               FROM raw_flight_searches f WHERE chat_session_id = ANY($ids)
                               ORDER BY created_at""", p)
        hotels = rows(con, """SELECT h.chat_session_id, h.destination_name, h.check_in_date, h.check_out_date,
                                     h.adults, h.created_at,
                                     (SELECT max(hotel_count) FROM raw_hotel_search_results r
                                       WHERE r.search_request_id = h.id) AS hotels
                              FROM raw_hotel_searches h WHERE chat_session_id = ANY($ids)
                              ORDER BY created_at""", p)
        feedback = rows(con, """SELECT chat_session_id, message_id, is_positive, comment, created_at
                                FROM raw_feedback WHERE chat_session_id = ANY($ids)""", p)
        llm_fail = rows(con, """SELECT chat_session_id, task, count(*) AS n FROM raw_llm_calls
                                WHERE NOT success AND chat_session_id = ANY($ids) GROUP BY 1, 2""", p)
        llm_calls = rows(con, """SELECT chat_session_id, task, model, success, error, latency_ms, created_at
                                 FROM raw_llm_calls WHERE chat_session_id = ANY($ids) ORDER BY created_at""", p)
        # events and bookings that carry no chat id are matched by user and the chat's time window
        window = """WITH w AS (SELECT chat_session_id, min(user_id) FILTER (WHERE role = 'USER') AS user_id,
                                      min(created_at) - INTERVAL '5 minutes' AS t0, max(created_at) + INTERVAL '60 minutes' AS t1
                               FROM messages WHERE chat_session_id = ANY($ids) GROUP BY 1)"""
        analytics = rows(con, f"""{window}
            SELECT w.chat_session_id AS sid, e.event_name, e.event_at AS created_at, e.platform, e.app_version,
                   e.properties
            FROM raw_analytics_events e JOIN w
              ON (e.chat_session_id = w.chat_session_id)
              OR (e.chat_session_id IS NULL AND e.user_id = w.user_id AND e.event_at BETWEEN w.t0 AND w.t1)
            ORDER BY e.event_at""", p, json_cols=("properties",))
        bookings = rows(con, f"""{window}
            SELECT w.chat_session_id AS sid, b.status, b.payment_status, b.failure_reason, b.provider, b.created_at
            FROM raw_flight_bookings b JOIN w
              ON (b.chat_session_id = w.chat_session_id)
              OR (b.chat_session_id IS NULL AND b.user_id = w.user_id AND b.created_at BETWEEN w.t0 AND w.t1)
            WHERE b.status = 'FAILED' OR b.failure_reason IS NOT NULL
            ORDER BY b.created_at""", p)

    by: dict[str, dict] = {f["session_id"]: {"facts": f, "messages": [], "events": [], "feedback": [], "llm_failures": [],
                                             "llm_calls": [], "analytics": [], "bookings": []}
                           for f in facts}
    for m in msgs:
        if m["chat_session_id"] in by:
            by[m["chat_session_id"]]["messages"].append(m)
    for i in intents:
        if i["chat_session_id"] in by:
            by[i["chat_session_id"]]["events"].append({"kind": "intent", "at": i["created_at"], "message_id": i["message_id"],
                                                        "text": f"EXTRACTED INTENT ({i['intent_type']}, conf {float(i['confidence'] or 0):.2f}): {intent_summary(i['parameters'])}",
                                                        "raw": i})
    for f in flights:
        if f["chat_session_id"] in by:
            pax = f"{f['adults']} adult(s)" + (f", {f['children']} child" if f["children"] else "") + (f", {f['infants']} infant" if f["infants"] else "")
            res = f"{f['options']} options" if f["result_sets"] else "no result stored"
            by[f["chat_session_id"]]["events"].append({
                "kind": "flight_search", "at": f["created_at"], "raw": f,
                "text": f"FLIGHT SEARCH: {f['origin']} → {f['destination']} on {f['departure_date']}"
                        f"{' return ' + str(f['return_date']) if f['return_date'] else ' one-way'}, {pax}, {f['cabin_class']} → {res}"})
    for h in hotels:
        if h["chat_session_id"] in by:
            by[h["chat_session_id"]]["events"].append({
                "kind": "hotel_search", "at": h["created_at"], "raw": h,
                "text": f"HOTEL SEARCH: {h['destination_name']} {h['check_in_date']} – {h['check_out_date']}, {h['adults']} adult(s) → {h['hotels'] or 0} hotels"})
    for fb in feedback:
        if fb["chat_session_id"] in by:
            by[fb["chat_session_id"]]["feedback"].append(fb)
    for lf in llm_fail:
        if lf["chat_session_id"] in by:
            by[lf["chat_session_id"]]["llm_failures"].append(lf)
    for lc in llm_calls:
        b = by.get(lc["chat_session_id"])
        if not b:
            continue
        b["llm_calls"].append(lc)
        if not lc["success"]:
            b["events"].append({"kind": "llm_failure", "at": lc["created_at"], "raw": lc,
                                "text": f"LLM CALL FAILED: {lc['task']} ({_short_model(lc['model'])}, {lc['latency_ms']} ms): "
                                        f"{(lc['error'] or 'unknown error').strip()[:180]}"})
    for e in analytics:
        b = by.get(e["sid"])
        if not b:
            continue
        b["analytics"].append(e)
        text = analytics_text(e)
        if text:
            b["events"].append({"kind": "analytics", "at": e["created_at"], "raw": e, "text": text})
    for bk in bookings:
        b = by.get(bk["sid"])
        if b:
            b["bookings"].append(bk)
            b["events"].append({"kind": "booking_failure", "at": bk["created_at"], "raw": bk,
                                "text": f"BOOKING {bk['status']} (payment {bk['payment_status'] or 'n/a'}): "
                                        f"{(bk['failure_reason'] or '').strip()[:220]}"})
    for b in by.values():
        plats = [(e.get("platform"), e.get("app_version")) for e in b["analytics"] if e.get("platform")]
        b["facts"]["platform"] = max(set(plats), key=plats.count)[0] if plats else None
        b["facts"]["app_version"] = max(set(plats), key=plats.count)[1] if plats else None

    bundles = []
    for sid, b in by.items():
        b["turns"] = build_turns(b)
        bundles.append(b)
    bundles.sort(key=lambda b: b["facts"]["created_at"])
    return bundles


def build_turns(b: dict) -> list[dict]:
    """Numbered message turns (t=1..n) with intents/searches attached as sub-events."""
    msg_events: dict[str, list] = {}
    loose = []
    for e in b["events"]:
        if e["kind"] == "intent" and e.get("message_id"):
            msg_events.setdefault(e["message_id"], []).append(e)
        else:
            loose.append(e)
    fb_by_msg = {f["message_id"]: f for f in b["feedback"]}
    turns: list[dict] = []
    msgs = b["messages"]
    li = 0
    loose.sort(key=lambda e: e["at"])
    for n, m in enumerate(msgs, start=1):
        nxt = msgs[n]["created_at"] if n < len(msgs) else None
        card = card_summary(m["card_type"], m.get("card_data"), m.get("card_shape"))
        t = {"t": n, "role": m["role"], "text": (m["content"] or "")[:2000], "card": card, "card_type": m["card_type"],
             "kind": m["kind"], "msg_id": m["id"], "at": m["created_local"], "at_utc": m["created_at"], "events": []}
        for e in msg_events.get(m["id"], []):
            t["events"].append({"kind": e["kind"], "text": e["text"]})
        # attach searches/unlinked intents that happened after this message and before the next one
        while li < len(loose) and (nxt is None or loose[li]["at"] < nxt):
            if loose[li]["at"] >= m["created_at"] or n == 1:
                t["events"].append({"kind": loose[li]["kind"], "text": loose[li]["text"]})
            li += 1
        fb = fb_by_msg.get(m["id"])
        if fb:
            t["events"].append({"kind": "feedback",
                                "text": f"USER FEEDBACK: {'👍' if fb['is_positive'] else '👎'} {fb.get('comment') or ''}".strip()})
        turns.append(t)
    return turns


def render_for_llm(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        body = t["text"].strip()
        if t["card"]:
            body = (body + " " if body else "") + t["card"]
        at = str(t.get("at") or "")[:16].replace("T", " ")
        lines.append(f"[t{t['t']} · {at}] {t['role']}: {body}")
        for e in t["events"]:
            lines.append(f"      · {e['text']}")
    return "\n".join(lines)
