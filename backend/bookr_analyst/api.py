"""Bookr Analyst API.

Run (dev):  uvicorn bookr_analyst.api:app --reload --port 8000
Serves the built React app from ../frontend/dist when it exists.
Data lives in Postgres (ANALYST_DATABASE_URL), see db.py.
"""
from __future__ import annotations

import base64
import secrets
import threading
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT, settings
from .db import connect, is_synced, one, rows

app = FastAPI(title="Bookr Analyst")
OFF = int(settings.tz_offset_minutes)

# Optional login for hosted deployments ---------------------------------------


@app.get("/api/health", include_in_schema=False)
def health():
    return {"ok": True}


if settings.basic_auth_password:
    _expected = f"{settings.basic_auth_user}:{settings.basic_auth_password}".encode()

    @app.middleware("http")
    async def _basic_auth(request: Request, call_next):
        if request.url.path != "/api/health":
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            try:
                given = base64.b64decode(token) if scheme.lower() == "basic" else b""
            except ValueError:
                given = b""
            if not secrets.compare_digest(given, _expected):
                return Response("Login required", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Bookr Analyst"'})
        return await call_next(request)

# Filters --------------------------------------------------------------------

# NOT EXISTS (not a scalar subquery) so Postgres can run it as one hash anti-join instead of a lookup per row
NOT_INTERNAL = "NOT EXISTS (SELECT 1 FROM users u WHERE u.user_id = {alias}.user_id AND u.is_internal)"


def _internal(alias: str, exclude: bool) -> str:
    return NOT_INTERNAL.format(alias=alias) if exclude else "true"


def _window(con, days: int) -> dict:
    synced_at = con.execute("SELECT synced_at FROM sync_meta").fetchone()[0]
    end = synced_at + timedelta(seconds=1)
    if days and days > 0:
        start = end - timedelta(days=days)
        prev_start = start - timedelta(days=days)
    else:
        first = con.execute("SELECT min(created_at) FROM raw_chat_sessions").fetchone()[0] or end
        start, prev_start = first, None
    return {"start": start, "end": end, "prev_start": prev_start, "synced_at": synced_at}


# Meta & sync ----------------------------------------------------------------

_sync_state = {"running": False, "last_error": None, "last_result": None}


@app.get("/api/meta")
def meta():
    try:
        with connect() as con:
            m = one(con, "SELECT * FROM sync_meta", json_cols=("row_counts",))
            internal = one(con, "SELECT count(*) FILTER (WHERE is_internal) AS internal_users, count(*) AS users FROM users")
    except FileNotFoundError:
        m, internal = None, None
    return {"sync": m, "users": internal, "tz_offset_minutes": OFF,
            "internal_rules": {"user_ids": len(settings.internal_user_ids),
                               "email_domains": list(settings.internal_email_domains)},
            "sync_running": _sync_state["running"], "last_sync_error": _sync_state["last_error"]}


def _run_sync_bg():
    from .sync import run_sync
    try:
        _sync_state["last_result"] = run_sync()
        _sync_state["last_error"] = None
    except Exception as e:  # surfaced in /api/meta
        _sync_state["last_error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        _sync_state["running"] = False


@app.post("/api/sync")
def trigger_sync():
    if _sync_state["running"]:
        return {"started": False, "reason": "already running"}
    _sync_state["running"] = True
    threading.Thread(target=_run_sync_bg, daemon=True).start()
    return {"started": True}


# Overview -------------------------------------------------------------------

def _kpis(con, start, end, exclude: bool) -> dict:
    p = {"s": start, "e": end}
    return one(con, f"""
        SELECT
          (SELECT count(*) FROM session_facts sf WHERE created_at >= $s AND created_at < $e
              AND (NOT $x OR NOT is_internal)) AS sessions,
          (SELECT count(DISTINCT user_id) FROM messages m WHERE role = 'USER' AND created_at >= $s AND created_at < $e
              AND {_internal('m', exclude)}) AS active_users,
          (SELECT count(*) FROM messages m WHERE role = 'USER' AND created_at >= $s AND created_at < $e
              AND {_internal('m', exclude)}) AS user_messages,
          (SELECT count(*) FROM raw_flight_searches f WHERE created_at >= $s AND created_at < $e
              AND {_internal('f', exclude)}) AS flight_searches,
          (SELECT count(*) FROM raw_hotel_searches h WHERE created_at >= $s AND created_at < $e
              AND {_internal('h', exclude)}) AS hotel_searches,
          (SELECT count(*) FROM booking_ops b WHERE created_at >= $s AND created_at < $e
              AND (NOT $x OR NOT is_internal)) AS booking_attempts,
          (SELECT count(*) FROM booking_ops b WHERE status = 'BOOKED' AND created_at >= $s AND created_at < $e
              AND (NOT $x OR NOT is_internal)) AS bookings,
          (SELECT coalesce(sum(amount_payable), 0) FROM booking_ops b WHERE status = 'BOOKED'
              AND created_at >= $s AND created_at < $e AND (NOT $x OR NOT is_internal)) AS gmv,
          (SELECT count(*) FROM session_facts WHERE n_booked > 0 AND created_at >= $s AND created_at < $e
              AND (NOT $x OR NOT is_internal)) AS sessions_booked,
          (SELECT coalesce(sum(cost_usd), 0) FROM raw_llm_calls l WHERE created_at >= $s AND created_at < $e
              AND {_internal('l', exclude)}) AS llm_cost_usd
    """, {**p, "x": exclude})


@app.get("/api/overview")
def overview(days: int = 30, exclude_internal: bool = True):
    with connect() as con:
        w = _window(con, days)
        cur = _kpis(con, w["start"], w["end"], exclude_internal)
        prev = _kpis(con, w["prev_start"], w["start"], exclude_internal) if w["prev_start"] else None
        for k in (cur, prev):
            if k:
                k["conversion_pct"] = round(100 * k["sessions_booked"] / k["sessions"], 1) if k["sessions"] else 0
                k["avg_booking_value"] = round(k["gmv"] / k["bookings"]) if k["bookings"] else 0
        x = exclude_internal
        p = {"s": w["start"], "e": w["end"], "x": x}
        daily = rows(con, f"""
            WITH days AS (
                SELECT CAST(r AS DATE) AS d
                FROM generate_series(date_trunc('day', CAST($s AS TIMESTAMP) + INTERVAL '{OFF} minutes'),
                                     CAST($e AS TIMESTAMP) + INTERVAL '{OFF} minutes', INTERVAL '1 day') t(r)
                WHERE r < CAST($e AS TIMESTAMP) + INTERVAL '{OFF} minutes'),
            s AS (SELECT CAST(created_local AS DATE) d, count(*) n FROM session_facts
                  WHERE created_at >= $s AND created_at < $e AND (NOT $x OR NOT is_internal) GROUP BY 1),
            fs AS (SELECT CAST(created_at + INTERVAL '{OFF} minutes' AS DATE) d, count(*) n FROM raw_flight_searches f
                   WHERE created_at >= $s AND created_at < $e AND {_internal('f', x)} GROUP BY 1),
            hs AS (SELECT CAST(created_at + INTERVAL '{OFF} minutes' AS DATE) d, count(*) n FROM raw_hotel_searches h
                   WHERE created_at >= $s AND created_at < $e AND {_internal('h', x)} GROUP BY 1),
            b AS (SELECT CAST(created_local AS DATE) d, count(*) attempts,
                         count(*) FILTER (WHERE status = 'BOOKED') booked,
                         coalesce(sum(amount_payable) FILTER (WHERE status = 'BOOKED'), 0) gmv
                  FROM booking_ops WHERE created_at >= $s AND created_at < $e AND (NOT $x OR NOT is_internal) GROUP BY 1)
            SELECT days.d AS date, coalesce(s.n, 0) AS sessions, coalesce(fs.n, 0) AS flight_searches,
                   coalesce(hs.n, 0) AS hotel_searches, coalesce(b.attempts, 0) AS booking_attempts,
                   coalesce(b.booked, 0) AS bookings, coalesce(b.gmv, 0) AS gmv
            FROM days LEFT JOIN s USING (d) LEFT JOIN fs USING (d) LEFT JOIN hs USING (d) LEFT JOIN b USING (d)
            ORDER BY 1
        """, p)
        routes = rows(con, f"""
            SELECT origin || ' → ' || destination AS route, count(*) AS searches, count(DISTINCT user_id) AS users
            FROM raw_flight_searches f WHERE created_at >= $s AND created_at < $e AND {_internal('f', x)}
            GROUP BY 1 ORDER BY 2 DESC LIMIT 8""", {"s": w["start"], "e": w["end"]})
        hotels = rows(con, f"""
            SELECT trim(split_part(destination_name, ',', 1)) AS destination, count(*) AS searches,
                   round(avg(CAST(check_out_date AS DATE) - CAST(check_in_date AS DATE)), 1) AS avg_nights,
                   round(avg(adults), 1) AS avg_adults
            FROM raw_hotel_searches h WHERE created_at >= $s AND created_at < $e AND {_internal('h', x)}
            GROUP BY 1 ORDER BY 2 DESC LIMIT 8""", {"s": w["start"], "e": w["end"]})
        ops = one(con, """
            SELECT count(*) FILTER (WHERE severity = 'critical') AS critical,
                   count(*) FILTER (WHERE severity = 'warning') AS warning,
                   coalesce(sum(amount_payable) FILTER (WHERE severity = 'critical'), 0) AS money_at_risk
            FROM booking_ops WHERE (NOT $x OR NOT is_internal)""", {"x": x})
    return {"window": {k: (v.isoformat() if v else None) for k, v in w.items()},
            "kpis": cur, "previous": prev, "daily": daily, "top_routes": routes,
            "top_hotel_destinations": hotels, "ops": ops}


# Funnel ---------------------------------------------------------------------

@app.get("/api/funnel")
def funnel(days: int = 30, exclude_internal: bool = True, flow: str | None = None):
    with connect() as con:
        w = _window(con, days)
        p = {"s": w["start"], "e": w["end"], "x": exclude_internal, "flow": flow}
        base = """FROM session_facts WHERE created_at >= $s AND created_at < $e
                  AND (NOT $x OR NOT is_internal) AND ($flow IS NULL OR current_flow = $flow)"""
        stages = rows(con, f"""
            SELECT st.level, st.name,
                   (SELECT count(*) {base} AND stage_level >= st.level) AS sessions,
                   (SELECT count(*) {base} AND stage_level = st.level) AS ended_here
            FROM stages st ORDER BY st.level""", p)
        top = stages[0]["sessions"] if stages else 0
        for i, s in enumerate(stages):
            prev_n = stages[i - 1]["sessions"] if i else s["sessions"]
            s["pct_of_start"] = round(100 * s["sessions"] / top, 1) if top else 0
            s["pct_of_prev"] = round(100 * s["sessions"] / prev_n, 1) if prev_n else 0
        by_flow = rows(con, f"""
            SELECT current_flow AS flow, count(*) AS sessions,
                   count(*) FILTER (WHERE stage_level >= 4) AS searched,
                   count(*) FILTER (WHERE stage_level >= 5) AS results_shown,
                   count(*) FILTER (WHERE stage_level >= 6) AS booking_attempted,
                   count(*) FILTER (WHERE stage_level >= 7) AS booked,
                   round(avg(n_user_msgs), 1) AS avg_user_msgs
            {base} GROUP BY 1 ORDER BY 2 DESC""", p)
        weekly = rows(con, f"""
            SELECT CAST(date_trunc('week', created_local) AS DATE) AS week, count(*) AS sessions,
                   count(*) FILTER (WHERE stage_level >= 3) AS intent_captured,
                   count(*) FILTER (WHERE stage_level >= 4) AS searched,
                   count(*) FILTER (WHERE stage_level >= 6) AS booking_attempted,
                   count(*) FILTER (WHERE stage_level >= 7) AS booked
            {base} GROUP BY 1 ORDER BY 1""", p)
        ux = one(con, f"""
            SELECT round(avg(n_user_msgs), 1) AS avg_user_msgs_per_session,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY user_msgs_before_search) FILTER (WHERE first_search_at IS NOT NULL) AS median_user_msgs_to_search,
                   round(CAST(percentile_cont(0.5) WITHIN GROUP (ORDER BY minutes_to_first_search)
                              FILTER (WHERE minutes_to_first_search >= 0) AS NUMERIC), 1) AS median_minutes_to_search,
                   round(100.0 * count(*) FILTER (WHERE current_flow = 'unresolved') / nullif(count(*), 0), 1) AS pct_unresolved_flow,
                   round(100.0 * count(*) FILTER (WHERE n_user_msgs > 0 AND stage_level <= 3) / nullif(count(*), 0), 1) AS pct_no_search,
                   count(*) FILTER (WHERE n_failure_msgs > 0) AS sessions_with_failures
            {base}""", p)
    return {"stages": stages, "by_flow": by_flow, "weekly": weekly, "ux": ux}


# Booking & payment ops ------------------------------------------------------

@app.get("/api/ops")
def ops(days: int = 0, exclude_internal: bool = True):
    with connect() as con:
        w = _window(con, days)
        p = {"s": w["start"], "e": w["end"], "x": exclude_internal}
        base = "FROM booking_ops WHERE created_at >= $s AND created_at < $e AND (NOT $x OR NOT is_internal)"
        summary = one(con, f"""
            SELECT count(*) AS attempts,
                   count(*) FILTER (WHERE status = 'BOOKED') AS booked,
                   count(*) FILTER (WHERE status = 'FAILED') AS failed,
                   count(*) FILTER (WHERE status LIKE 'PENDING%') AS pending,
                   count(*) FILTER (WHERE severity = 'critical') AS critical,
                   count(*) FILTER (WHERE severity = 'warning') AS warning,
                   coalesce(sum(amount_payable) FILTER (WHERE severity = 'critical'), 0) AS money_at_risk,
                   coalesce(sum(amount_payable) FILTER (WHERE status = 'BOOKED'), 0) AS gmv,
                   round(100.0 * count(*) FILTER (WHERE status = 'BOOKED') / nullif(count(*), 0), 1) AS success_rate
            {base}""", p)
        issues = rows(con, f"""
            SELECT id, created_local, status, payment_status, amount_payable, issue, severity,
                   hours_since_update, origin, destination, airline, provider_booking_id,
                   failure_reason, failure_category, chat_session_id, user_id, has_pnr, ticket_count,
                   confirmation_email_sent_at
            {base} AND severity <> 'ok'
            ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, created_at DESC""", p)
        matrix = rows(con, f"""
            SELECT status, coalesce(payment_status, '—') AS payment_status, count(*) AS bookings,
                   coalesce(sum(amount_payable), 0) AS amount
            {base} GROUP BY 1, 2 ORDER BY 3 DESC""", p)
        failures = rows(con, f"""
            SELECT coalesce(failure_category, 'Unknown') AS category, count(*) AS bookings
            {base} AND status = 'FAILED' GROUP BY 1 ORDER BY 2 DESC""", p)
        daily = rows(con, f"""
            SELECT CAST(created_local AS DATE) AS date,
                   count(*) FILTER (WHERE status = 'BOOKED') AS booked,
                   count(*) FILTER (WHERE status = 'FAILED') AS failed,
                   count(*) FILTER (WHERE status LIKE 'PENDING%') AS pending
            {base} GROUP BY 1 ORDER BY 1""", p)
    return {"summary": summary, "issues": issues, "matrix": matrix, "failures": failures, "daily": daily}


# Conversation explorer ------------------------------------------------------

OUTCOMES = {
    "booked": "n_booked > 0",
    "booking_failed": "n_failed_bookings > 0 AND n_booked = 0",
    "no_search": "n_user_msgs > 0 AND stage_level <= 3",
    "unresolved": "current_flow = 'unresolved'",
    "failure_msgs": "n_failure_msgs > 0",
    "group": "is_group_chat",
}


@app.get("/api/sessions")
def sessions(days: int = 30, exclude_internal: bool = True, q: str | None = None,
             flow: str | None = None, outcome: str | None = None, stage: int | None = None,
             user: str | None = None, sort: str = "newest", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    if outcome and outcome not in OUTCOMES:
        raise HTTPException(400, f"outcome must be one of {list(OUTCOMES)}")
    order = {"newest": "created_at DESC", "oldest": "created_at ASC",
             "most_messages": "n_user_msgs DESC, created_at DESC",
             "stage": "stage_level DESC, created_at DESC"}.get(sort, "created_at DESC")
    with connect() as con:
        w = _window(con, days)
        where = f"""created_at >= $s AND created_at < $e AND (NOT $x OR NOT is_internal)
                    AND ($flow IS NULL OR current_flow = $flow) AND ($stage IS NULL OR stage_level = $stage)
                    AND ($user IS NULL OR user_id = $user)
                    AND ({OUTCOMES[outcome] if outcome else 'true'})
                    AND ($q IS NULL OR title ILIKE '%' || $q || '%' OR session_id = $q OR EXISTS (
                        SELECT 1 FROM messages m WHERE m.chat_session_id = sf.session_id AND m.content ILIKE '%' || $q || '%'))"""
        p = {"s": w["start"], "e": w["end"], "x": exclude_internal, "flow": flow, "stage": stage, "q": q or None,
             "user": user or None}
        total = con.execute(f"SELECT count(*) FROM session_facts sf WHERE {where}", p).fetchone()[0]
        items = rows(con, f"""
            SELECT sf.session_id, sf.created_local, sf.title, sf.user_id, sf.current_flow, sf.is_group_chat,
                   sf.stage_level, st.name AS stage, sf.n_user_msgs, sf.n_messages,
                   sf.n_flight_searches, sf.n_hotel_searches, sf.n_booking_attempts, sf.n_booked,
                   sf.n_failed_bookings, sf.gmv, sf.n_failure_msgs
            FROM session_facts sf JOIN stages st ON st.level = sf.stage_level
            WHERE {where} ORDER BY {order} LIMIT {page_size} OFFSET {(page - 1) * page_size}""", p)
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@app.get("/api/sessions/{session_id}")
def session_detail(session_id: str):
    with connect() as con:
        facts = one(con, """SELECT sf.*, st.name AS stage FROM session_facts sf
                            JOIN stages st ON st.level = sf.stage_level WHERE session_id = $id""", {"id": session_id})
        if not facts:
            raise HTTPException(404, "session not found")
        p = {"id": session_id}
        flow = one(con, "SELECT * FROM raw_flow_state WHERE chat_session_id = $id", p,
                   json_cols=("flight_state", "multi_city_state", "travel_intent"))
        msgs = rows(con, f"""
            SELECT id, role, content, kind, card_type, card_superseded, card_data, card_shape, bookkr_invoked,
                   meta_rest, created_at, created_local
            FROM messages WHERE chat_session_id = $id ORDER BY created_at""", p,
                    json_cols=("card_data", "card_shape", "meta_rest"))
        intents = rows(con, f"""
            SELECT id, message_id, intent_type, confidence, parameters, created_at,
                   created_at + INTERVAL '{OFF} minutes' AS created_local
            FROM raw_intents WHERE chat_session_id = $id AND deleted_at IS NULL ORDER BY created_at""", p,
                       json_cols=("parameters",))
        flights = rows(con, f"""
            SELECT f.id, f.origin, f.destination, f.departure_date, f.return_date, f.adults, f.children,
                   f.infants, f.cabin_class, f.is_round_trip, f.search_hash, f.created_at,
                   f.created_at + INTERVAL '{OFF} minutes' AS created_local,
                   (SELECT count(*) FROM raw_flight_search_results r WHERE r.search_request_id = f.id) AS result_sets,
                   (SELECT coalesce(sum(option_count), 0) FROM raw_flight_search_results r WHERE r.search_request_id = f.id) AS options
            FROM raw_flight_searches f WHERE chat_session_id = $id ORDER BY created_at""", p)
        hotels = rows(con, f"""
            SELECT h.id, h.destination_name, h.check_in_date, h.check_out_date, h.adults, h.children, h.filters,
                   h.created_at, h.created_at + INTERVAL '{OFF} minutes' AS created_local,
                   (SELECT max(hotel_count) FROM raw_hotel_search_results r WHERE r.search_request_id = h.id) AS hotels
            FROM raw_hotel_searches h WHERE chat_session_id = $id ORDER BY created_at""", p, json_cols=("filters",))
        bookings = rows(con, """
            SELECT id, status, payment_status, amount_payable, wallet_amount, issue, severity, origin, destination,
                   airline, fare_name, provider_booking_id, failure_reason, has_pnr, ticket_count,
                   confirmation_email_sent_at, created_at, created_local
            FROM booking_ops WHERE chat_session_id = $id ORDER BY created_at""", p)
        llm = rows(con, """
            SELECT task, count(*) AS calls, round(CAST(sum(cost_usd) AS NUMERIC), 4) AS cost_usd, round(avg(latency_ms)) AS avg_latency_ms,
                   count(*) FILTER (WHERE NOT success) AS failures
            FROM raw_llm_calls WHERE chat_session_id = $id GROUP BY 1 ORDER BY 2 DESC""", p)

    timeline = (
        [{"type": "message", **m} for m in msgs]
        + [{"type": "intent", **i} for i in intents]
        + [{"type": "flight_search", **f} for f in flights]
        + [{"type": "hotel_search", **h} for h in hotels]
        + [{"type": "booking", **b} for b in bookings]
    )
    order = {"message": 0, "intent": 1, "flight_search": 2, "hotel_search": 2, "booking": 3}
    timeline.sort(key=lambda e: (e["created_at"], order[e["type"]]))
    return {"facts": facts, "flow_state": flow, "timeline": timeline, "llm": llm}


# Users & retention ----------------------------------------------------------

ACT = f"""
    WITH act AS (   -- one row per user per active IST day (sent at least one message)
        SELECT m.user_id, CAST(m.created_local AS DATE) AS d, count(*) AS msgs
        FROM messages m
        WHERE m.role = 'USER' AND m.user_id IS NOT NULL AND {{internal}}
        GROUP BY 1, 2),
    firsts AS (SELECT user_id, min(d) AS first_day, max(d) AS last_day, count(*) AS total_active_days FROM act GROUP BY 1)
"""


def _user_kpis(con, s, e, exclude: bool) -> dict:
    p = {"sd": s, "ed": e}
    return one(con, ACT.format(internal=_internal("m", exclude)) + """
        , win AS (SELECT a.user_id, count(*) AS days FROM act a WHERE a.d >= $sd AND a.d < $ed GROUP BY 1)
        SELECT count(*) AS active_users,
               count(*) FILTER (WHERE f.first_day >= $sd) AS new_users,
               count(*) FILTER (WHERE f.first_day < $sd) AS returning_users,
               count(*) FILTER (WHERE w.days >= 2) AS repeat_in_period,
               round(avg(w.days), 2) AS avg_active_days
        FROM win w JOIN firsts f USING (user_id)""", p)


@app.get("/api/users")
def users_report(days: int = 30, exclude_internal: bool = True):
    x = exclude_internal
    with connect() as con:
        w = _window(con, days)
        # day-level boundaries in IST
        to_d = lambda t: (t + timedelta(minutes=OFF)).date() if t else None  # noqa: E731
        sd, ed = to_d(w["start"]), to_d(w["end"]) + timedelta(days=1)
        psd = to_d(w["prev_start"]) if w["prev_start"] else None
        cur = _user_kpis(con, sd, ed, x)
        prev = _user_kpis(con, psd, sd, x) if psd else None
        for k in (cur, prev):
            if k:
                k["returning_pct"] = round(100 * k["returning_users"] / k["active_users"], 1) if k["active_users"] else 0
        A = ACT.format(internal=_internal("m", x))
        p = {"sd": sd, "ed": ed}
        extra = one(con, A + """
            , gaps AS (SELECT user_id, d, d - lag(d) OVER (PARTITION BY user_id ORDER BY d) AS gap FROM act),
            ret AS (SELECT f.user_id, f.first_day,
                           bool_or(a.d > f.first_day AND a.d <= f.first_day + 7) AS r7,
                           bool_or(a.d > f.first_day AND a.d <= f.first_day + 30) AS r30,
                           bool_or(a.d > f.first_day) AS ever
                    FROM firsts f JOIN act a USING (user_id) GROUP BY 1, 2),
            maxd AS (SELECT max(d) AS md FROM act),
            sess AS (SELECT user_id, count(*) AS sessions FROM session_facts
                     WHERE n_user_msgs > 0 AND CAST(created_local AS DATE) >= $sd AND CAST(created_local AS DATE) < $ed
                       AND (NOT $x OR NOT is_internal) GROUP BY 1),
            bk AS (SELECT user_id, count(*) AS bookings FROM booking_ops WHERE status = 'BOOKED'
                     AND (NOT $x OR NOT is_internal) GROUP BY 1)
            SELECT
              (SELECT round(avg(sessions), 2) FROM sess) AS avg_sessions_per_user,
              (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) FROM gaps
                 WHERE gap IS NOT NULL AND d >= $sd AND d < $ed) AS median_days_between_visits,
              (SELECT round(100.0 * count(*) FILTER (WHERE r7) / nullif(count(*), 0), 1) FROM ret, maxd
                 WHERE first_day <= md - 7) AS returned_7d_pct,
              (SELECT count(*) FROM ret, maxd WHERE first_day <= md - 7) AS returned_7d_base,
              (SELECT round(100.0 * count(*) FILTER (WHERE r30) / nullif(count(*), 0), 1) FROM ret, maxd
                 WHERE first_day <= md - 30) AS returned_30d_pct,
              (SELECT count(*) FROM ret, maxd WHERE first_day <= md - 30) AS returned_30d_base,
              (SELECT round(100.0 * count(*) FILTER (WHERE ever) / nullif(count(*), 0), 1) FROM ret) AS ever_returned_pct,
              (SELECT count(*) FROM bk) AS booking_users,
              (SELECT count(*) FROM bk WHERE bookings >= 2) AS repeat_bookers
        """, {**p, "x": x})
        daily = rows(con, A + """
            , days AS (SELECT CAST(r AS DATE) AS d
                      FROM generate_series(CAST($sd AS TIMESTAMP), CAST($ed AS TIMESTAMP) - INTERVAL '1 day',
                                           INTERVAL '1 day') t(r))
            SELECT days.d AS date,
                   count(a.user_id) FILTER (WHERE f.first_day = a.d) AS new_users,
                   count(a.user_id) FILTER (WHERE f.first_day < a.d) AS returning_users
            FROM days LEFT JOIN act a ON a.d = days.d LEFT JOIN firsts f USING (user_id)
            GROUP BY 1 ORDER BY 1""", p)
        frequency = rows(con, A + """
            , win AS (SELECT user_id, count(*) AS n FROM act WHERE d >= $sd AND d < $ed GROUP BY 1)
            SELECT CASE WHEN n = 1 THEN '1 day' WHEN n = 2 THEN '2 days' WHEN n <= 5 THEN '3–5 days'
                        WHEN n <= 10 THEN '6–10 days' ELSE '11+ days' END AS bucket,
                   min(n) AS ord, count(*) AS users
            FROM win GROUP BY 1 ORDER BY 2""", p)
        cohorts = rows(con, A + """
            , c AS (SELECT f.user_id, CAST(date_trunc('week', CAST(f.first_day AS TIMESTAMP)) AS DATE) AS cohort,
                           (CAST(date_trunc('week', CAST(a.d AS TIMESTAMP)) AS DATE)
                            - CAST(date_trunc('week', CAST(f.first_day AS TIMESTAMP)) AS DATE)) / 7 AS wk
                    FROM firsts f JOIN act a USING (user_id))
            SELECT cohort, wk, count(DISTINCT user_id) AS users FROM c WHERE wk <= 7
            GROUP BY 1, 2 ORDER BY 1 DESC, 2""")
        by_session_n = rows(con, """
            WITH s AS (SELECT *, row_number() OVER (PARTITION BY user_id ORDER BY created_at) AS n
                       FROM session_facts WHERE n_user_msgs > 0 AND (NOT $x OR NOT is_internal))
            SELECT CASE WHEN n = 1 THEN '1st chat' WHEN n = 2 THEN '2nd chat' WHEN n <= 5 THEN '3rd–5th chat'
                        ELSE '6th+ chat' END AS bucket, min(n) AS ord, count(*) AS sessions,
                   round(100.0 * count(*) FILTER (WHERE stage_level >= 4) / count(*), 1) AS searched_pct,
                   round(100.0 * count(*) FILTER (WHERE stage_level >= 7) / count(*), 1) AS booked_pct,
                   round(avg(user_msgs_before_search) FILTER (WHERE first_search_at IS NOT NULL), 1) AS msgs_to_search
            FROM s WHERE CAST(created_local AS DATE) >= $sd AND CAST(created_local AS DATE) < $ed
            GROUP BY 1 ORDER BY 2""", {**p, "x": x})
        top = rows(con, A + """
            , win AS (SELECT user_id, count(*) AS active_days, sum(msgs) AS messages FROM act
                      WHERE d >= $sd AND d < $ed GROUP BY 1)
            SELECT w.user_id, u.is_guest, f.first_day, f.last_day, w.active_days, f.total_active_days, w.messages,
                   (SELECT count(*) FROM session_facts sf WHERE sf.user_id = w.user_id
                      AND CAST(sf.created_local AS DATE) >= $sd AND CAST(sf.created_local AS DATE) < $ed) AS sessions,
                   (SELECT count(*) FROM session_facts sf WHERE sf.user_id = w.user_id) AS sessions_all_time,
                   (SELECT count(*) FROM raw_flight_searches fs WHERE fs.user_id = w.user_id) +
                   (SELECT count(*) FROM raw_hotel_searches hs WHERE hs.user_id = w.user_id) AS searches_all_time,
                   (SELECT count(*) FROM booking_ops b WHERE b.user_id = w.user_id AND b.status = 'BOOKED') AS bookings_all_time,
                   (SELECT coalesce(sum(amount_payable), 0) FROM booking_ops b WHERE b.user_id = w.user_id AND b.status = 'BOOKED') AS gmv_all_time
            FROM win w JOIN firsts f USING (user_id) LEFT JOIN users u ON u.user_id = w.user_id
            ORDER BY w.active_days DESC, w.messages DESC LIMIT 25""", p)
    sizes = {}
    for r in cohorts:
        if r["wk"] == 0:
            sizes[r["cohort"]] = r["users"]
    grid: dict[str, dict] = {}
    for r in cohorts:
        g = grid.setdefault(r["cohort"], {"cohort": r["cohort"], "size": sizes.get(r["cohort"], 0), "weeks": {}})
        g["weeks"][r["wk"]] = round(100 * r["users"] / g["size"], 1) if g["size"] else None
    return {"kpis": cur, "previous": prev, "extra": extra, "daily": daily, "frequency": frequency,
            "cohorts": list(grid.values())[:10], "by_session_number": by_session_n, "top_users": top,
            "heavy_user_threshold": 30}


# Friction Finder ------------------------------------------------------------

from .friction_api import router as friction_router, start_daily_scheduler  # noqa: E402

app.include_router(friction_router)


@app.on_event("startup")
def _schedule():
    from .sync import run_sync
    start_daily_scheduler(run_sync)
    # Empty database: pull the first copy in the background instead of showing an empty app.
    try:
        if settings.source_database_url and not is_synced():
            trigger_sync()
    except Exception:  # database unreachable or not configured: the app still starts and /api/meta reports it
        traceback.print_exc()


# Static frontend ------------------------------------------------------------

DIST = PROJECT_ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        # never cache the app shell: after a rebuild the browser must pick up the new asset names
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
