"""Ops-board parity (TRACKER R15): everything the old Bookkr ops dashboard showed, on the synced copy.

Sections: bookings & money (flights + hotels), needs action, payments, support, checkout steps, growth (signups,
vouchers, wallet, traffic sources), audience (accounts vs guests, web vs app, DAU/WAU, returning, retained) and quality
(reply ratings, server errors, hotel catalogue sync). Every list row carries the ids a drill-down needs (chat id,
user id, booking id). "Now" is the last sync, so live sections are as fresh as the last Refresh data.

Definitions follow the old board's spec (docs/superpowers/specs/2026-11-03-ops-dashboard-v2-design.md in the Bookkr
repo): confirmed = flight BOOKED/TICKETED, hotel CONFIRMED; GBV includes the wallet's share; an abandoned flight
checkout is not a failure; payment success = AUTHORIZED/CAPTURED/REFUNDED/PARTIALLY_REFUNDED vs FAILED.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query

from .api import OFF, _prev, _window
from .db import connect, one, rows

router = APIRouter(prefix="/api")

LIVE_DAYS = 30
STUCK_MIN = 30
SUCCEEDED = "('AUTHORIZED', 'CAPTURED', 'REFUNDED', 'PARTIALLY_REFUNDED')"
GUEST = "(left({c}, 6) = 'guest_' OR EXISTS (SELECT 1 FROM users g WHERE g.user_id = {c} AND g.is_guest))"


def _not_internal(col: str, exclude: bool) -> str:
    return f"NOT EXISTS (SELECT 1 FROM users ux WHERE ux.user_id = {col} AND ux.is_internal)" if exclude else "true"


def _change(cur, prev) -> dict:
    """{value, prev, change}: change like the old board: +20%, −8%, new, or None."""
    if not isinstance(cur, (int, float)) or not isinstance(prev, (int, float)):
        return {"value": cur, "prev": prev, "change": None}
    if prev == 0:
        return {"value": cur, "prev": prev, "change": None if cur == 0 else "new"}
    pct = round(100 * (cur - prev) / prev)
    return {"value": cur, "prev": prev, "change": "0%" if pct == 0 else f"+{pct}%" if pct > 0 else f"−{-pct}%"}


def _compare(cur: dict | None, prev: dict | None) -> dict | None:
    if cur is None:
        return None
    return {k: _change(v, (prev or {}).get(k)) for k, v in cur.items()}


def _ist_day(col: str) -> str:
    return f"CAST({col} + INTERVAL '{OFF} minutes' AS DATE)"


# ---------------------------------------------------------------------------------------------------------------
# Bookings & money
# ---------------------------------------------------------------------------------------------------------------

def _money(con, s, e, x) -> dict:
    out = {}
    for product in ("flight", "hotel"):
        r = one(con, f"""
            SELECT count(*) FILTER (WHERE confirmed) AS confirmed,
                   count(*) FILTER (WHERE confirmed AND wallet_inr > 0) AS with_wallet,
                   count(*) FILTER (WHERE confirmed AND currency <> 'INR') AS not_inr,
                   coalesce(sum(value_inr) FILTER (WHERE confirmed AND currency = 'INR'), 0) AS gbv,
                   coalesce(sum(wallet_inr) FILTER (WHERE confirmed AND currency = 'INR'), 0) AS wallet,
                   count(*) FILTER (WHERE cancelled) AS cancelled, count(*) FILTER (WHERE failed) AS failed,
                   count(*) FILTER (WHERE abandoned) AS abandoned, count(*) AS attempts
            FROM bookings_all WHERE product = $p AND created_at >= $s AND created_at < $e AND (NOT $x OR NOT is_internal)""",
                {"p": product, "s": s, "e": e, "x": x})
        gbv, wallet = round(r["gbv"] or 0, 2), round(r["wallet"] or 0, 2)
        out[product] = {"confirmed": r["confirmed"], "with_wallet": r["with_wallet"],
                        "without_wallet": r["confirmed"] - r["with_wallet"], "gbv": gbv, "card": round(gbv - wallet, 2),
                        "wallet": wallet, "avg": round(gbv / (r["confirmed"] - r["not_inr"]), 2) if r["confirmed"] - r["not_inr"] else None,
                        "cancelled": r["cancelled"], "failed": r["failed"], "abandoned": r["abandoned"],
                        "attempts": r["attempts"], "not_inr": r["not_inr"]}
    out["total_gbv"] = round(out["flight"]["gbv"] + out["hotel"]["gbv"], 2)
    return out


@router.get("/money")
def money(days: int = 30, exclude_internal: bool = True):
    x = exclude_internal
    with connect() as con:
        w = _window(con, days)
        cur = _money(con, w["start"], w["end"], x)
        pw = _prev(w)
        prev = _money(con, pw[0], pw[1], x) if pw else None
        compared = {p: _compare(cur[p], (prev or {}).get(p)) for p in ("flight", "hotel")}
        compared["total_gbv"] = _change(cur["total_gbv"], (prev or {}).get("total_gbv"))
        p = {"s": w["start"], "e": w["end"], "x": x}
        daily = rows(con, f"""
            SELECT CAST(created_local AS DATE) AS date,
                   count(*) FILTER (WHERE confirmed AND product = 'flight') AS flights,
                   count(*) FILTER (WHERE confirmed AND product = 'hotel') AS hotels,
                   round(CAST(coalesce(sum(value_inr) FILTER (WHERE confirmed AND currency = 'INR'), 0) AS numeric), 0) AS gbv,
                   round(CAST(coalesce(sum(wallet_inr) FILTER (WHERE confirmed AND currency = 'INR'), 0) AS numeric), 0) AS wallet
            FROM bookings_all WHERE created_at >= $s AND created_at < $e AND (NOT $x OR NOT is_internal)
            GROUP BY 1 ORDER BY 1""", p)
        top_airlines = rows(con, """
            SELECT coalesce(airline, 'Unknown') AS label, count(*) AS bookings, round(CAST(sum(value_inr) AS numeric), 0) AS gbv
            FROM bookings_all WHERE product = 'flight' AND confirmed AND created_at >= $s AND created_at < $e
              AND (NOT $x OR NOT is_internal) GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 8""", p)
        top_routes = rows(con, """
            SELECT route AS label, count(*) AS bookings, round(CAST(sum(value_inr) AS numeric), 0) AS gbv
            FROM bookings_all WHERE product = 'flight' AND confirmed AND route IS NOT NULL AND created_at >= $s AND created_at < $e
              AND (NOT $x OR NOT is_internal) GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 8""", p)
        top_cities = rows(con, """
            SELECT city AS label, count(*) AS bookings, round(CAST(sum(value_inr) AS numeric), 0) AS gbv
            FROM bookings_all WHERE product = 'hotel' AND confirmed AND city IS NOT NULL AND created_at >= $s AND created_at < $e
              AND (NOT $x OR NOT is_internal) GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 8""", p)
        searches = one(con, f"""
            SELECT (SELECT count(*) FROM raw_flight_searches f WHERE created_at >= $s AND created_at < $e AND {_not_internal('f.user_id', x)}) AS flight_searches,
                   (SELECT count(*) FROM raw_hotel_searches h WHERE created_at >= $s AND created_at < $e AND {_not_internal('h.user_id', x)}) AS hotel_searches""", p)
    return {"numbers": compared, "has_previous": bool(pw), "daily": daily, "searches": searches,
            "top": {"airlines": top_airlines, "routes": top_routes, "cities": top_cities}}


BOOKING_OUTCOMES = {"confirmed": "confirmed", "failed": "failed", "cancelled": "cancelled", "abandoned": "abandoned",
                    "pending": "NOT (confirmed OR failed OR cancelled OR abandoned)", "wallet": "confirmed AND wallet_inr > 0"}


@router.get("/list/bookings")
def list_bookings(days: int = 30, exclude_internal: bool = True, product: str | None = None, outcome: str | None = None,
                  airline: str | None = None, city: str | None = None, route: str | None = None,
                  limit: int = Query(200, ge=1, le=1000)):
    """Drill-down: the bookings behind a number, each with its chat."""
    if outcome and outcome not in BOOKING_OUTCOMES:
        raise HTTPException(400, f"outcome must be one of {list(BOOKING_OUTCOMES)}")
    if product and product not in ("flight", "hotel"):
        raise HTTPException(400, "product must be flight or hotel")
    with connect() as con:
        w = _window(con, days)
        return {"items": rows(con, f"""
            SELECT product, id, user_id, chat_session_id, status, payment_status, value_inr, wallet_inr, airline, route, city,
                   failure_reason, created_local
            FROM bookings_all WHERE created_at >= $s AND created_at < $e AND (NOT $x OR NOT is_internal)
              AND ($p IS NULL OR product = $p) AND ({BOOKING_OUTCOMES[outcome] if outcome else 'true'})
              AND ($a IS NULL OR coalesce(airline, 'Unknown') = $a) AND ($c IS NULL OR city = $c) AND ($r IS NULL OR route = $r)
            ORDER BY created_at DESC LIMIT {limit}""",
            {"s": w["start"], "e": w["end"], "x": exclude_internal, "p": product, "a": airline, "c": city, "r": route})}


# ---------------------------------------------------------------------------------------------------------------
# Needs action (as of the last sync)
# ---------------------------------------------------------------------------------------------------------------

@router.get("/needs-action")
def needs_action(exclude_internal: bool = True):
    x = exclude_internal
    with connect() as con:
        now = con.execute("SELECT synced_at FROM sync_meta").fetchone()[0]
        p = {"now": now, "x": x}
        ni = _not_internal("b.user_id", x)
        # failed (or abandoned-looking) bookings of the last 30 days whose money is held or taken and not given back
        paid_not_booked = rows(con, f"""
            WITH failed AS (
                SELECT 'flight' AS product, 'FLIGHT' AS ref, b.id, b.user_id, b.chat_session_id, b.payment_order_id, b.updated_at
                FROM raw_flight_bookings b
                WHERE (b.status = 'FAILED' OR (b.status = 'PENDING_BOOK' AND b.payment_transaction_id IS NULL
                                               AND b.updated_at < CAST($now AS timestamp) - INTERVAL '{STUCK_MIN} minutes'))
                  AND b.created_at >= CAST($now AS timestamp) - INTERVAL '{LIVE_DAYS} days' AND {ni}
                UNION ALL
                SELECT 'hotel', 'HOTEL', b.id, b.user_id, b.chat_session_id, b.payment_order_id, b.updated_at
                FROM raw_hotel_bookings b
                WHERE (b.status IN ('FAILED', 'ABORTED') OR (b.status IN ('INITIATED', 'ORDER_CREATED')
                                               AND b.updated_at < CAST($now AS timestamp) - INTERVAL '{STUCK_MIN} minutes'))
                  AND b.created_at >= CAST($now AS timestamp) - INTERVAL '{LIVE_DAYS} days' AND {ni}),
            orders AS (
                SELECT f.*, o.id AS order_id, o.razorpay_order_id FROM failed f
                JOIN raw_payment_orders o ON o.id = f.payment_order_id OR (o.reference_type = f.ref AND o.reference_id = f.id)
                WHERE o.status <> 'VOIDED'),
            money AS (
                SELECT o.product, o.id, o.user_id, o.chat_session_id, o.updated_at, t.razorpay_payment_id AS payment_id,
                       t.amount, CASE WHEN t.status = 'CAPTURED' THEN 'captured' ELSE 'held' END AS state
                FROM orders o JOIN raw_payment_txns t ON t.order_id = o.order_id WHERE t.status IN ('AUTHORIZED', 'CAPTURED')
                UNION ALL
                SELECT o.product, o.id, o.user_id, o.chat_session_id, o.updated_at, h.razorpay_payment_id, h.amount,
                       CASE WHEN h.event = 'payment.captured' THEN 'captured' ELSE 'held' END
                FROM orders o JOIN raw_payment_webhooks h ON h.razorpay_order_id = o.razorpay_order_id
                WHERE h.event IN ('payment.authorized', 'payment.captured')),
            open_money AS (
                SELECT DISTINCT ON (id, payment_id) * FROM money m
                WHERE NOT EXISTS (SELECT 1 FROM raw_refunds r JOIN raw_payment_txns rt ON rt.id = r.transaction_id
                                  WHERE rt.razorpay_payment_id = m.payment_id AND r.status <> 'FAILED')
                  AND NOT EXISTS (SELECT 1 FROM raw_payment_webhooks rh WHERE rh.event IN ('refund.created', 'refund.processed')
                                  AND rh.razorpay_payment_id = m.payment_id)
                ORDER BY id, payment_id, (state = 'captured') DESC),
            wallet AS (
                SELECT f.product, f.id, f.user_id, f.chat_session_id, f.updated_at, NULL AS payment_id, e.amount, 'wallet' AS state
                FROM failed f JOIN raw_wallet_entries e ON e.reference_type = f.ref AND e.reference_id = f.id
                WHERE e.kind = 'DEBIT' AND e.status = 'HELD' AND e.updated_at < CAST($now AS timestamp) - INTERVAL '{STUCK_MIN} minutes')
            SELECT product, id AS booking_id, user_id, chat_session_id, payment_id, amount / 100.0 AS inr, state,
                   floor(extract(epoch FROM (CAST($now AS timestamp) - updated_at)) / 60) AS age_min
            FROM (SELECT * FROM open_money UNION ALL SELECT * FROM wallet) a ORDER BY age_min DESC LIMIT 50""", p)
        stuck = rows(con, f"""
            SELECT product, booking_id, user_id, chat_session_id, status, value_inr,
                   floor(extract(epoch FROM (CAST($now AS timestamp) - updated_at)) / 60) AS age_min
            FROM (SELECT 'flight' AS product, b.id AS booking_id, b.user_id, b.chat_session_id, b.status,
                         b.amount_payable AS value_inr, b.updated_at, b.created_at
                  FROM raw_flight_bookings b
                  WHERE ((b.status = 'PENDING_BOOK' AND b.payment_transaction_id IS NOT NULL
                          AND coalesce(b.payment_status, '') NOT IN ('FAILED', 'VOIDED')) OR b.status = 'PENDING') AND {ni}
                  UNION ALL
                  SELECT 'hotel', b.id, b.user_id, b.chat_session_id, b.status, b.amount / 100.0, b.updated_at, b.created_at
                  FROM raw_hotel_bookings b
                  WHERE b.status IN ('AUTHORIZED', 'BOOK_PENDING', 'CAPTURE_PENDING', 'VOID_PENDING') AND {ni}) s
            WHERE updated_at < CAST($now AS timestamp) - INTERVAL '{STUCK_MIN} minutes'
              AND created_at >= CAST($now AS timestamp) - INTERVAL '{LIVE_DAYS} days'
            ORDER BY age_min DESC LIMIT 50""", p)
        support = _support(con, now, x)
    return {"as_of": now.isoformat(), "paid_not_booked": paid_not_booked, "stuck": stuck, "support": support,
            "paid_not_booked_inr": round(sum(r["inr"] or 0 for r in paid_not_booked), 2)}


def _support(con, now, x) -> dict:
    p = {"now": now}
    s = one(con, f"""
        SELECT count(*) AS total, count(*) FILTER (WHERE priority = 'HIGH') AS high,
               count(*) FILTER (WHERE priority = 'MEDIUM') AS medium, count(*) FILTER (WHERE priority = 'LOW') AS low,
               floor(extract(epoch FROM (CAST($now AS timestamp) - min(last_message_at))) / 60) AS oldest_wait_min,
               count(*) FILTER (WHERE last_message_at < CAST($now AS timestamp) - INTERVAL '24 hours') AS over_24h
        FROM raw_support_tickets t WHERE status = 'OPEN' AND {_not_internal('t.user_id', x)}""", p)
    waiting = rows(con, f"""
        SELECT number, priority, source, user_id, created_at + INTERVAL '{OFF} minutes' AS created_local,
               floor(extract(epoch FROM (CAST($now AS timestamp) - last_message_at)) / 60) AS waiting_min
        FROM raw_support_tickets t WHERE status = 'OPEN' AND {_not_internal('t.user_id', x)}
        ORDER BY last_message_at LIMIT 50""", p)
    return {**(s or {}), "waiting": waiting}


# ---------------------------------------------------------------------------------------------------------------
# Payments and support (range)
# ---------------------------------------------------------------------------------------------------------------

def _pay_numbers(con, s, e, x) -> dict:
    r = one(con, f"""
        SELECT count(*) FILTER (WHERE t.status IN {SUCCEEDED}) AS succeeded, count(*) FILTER (WHERE t.status = 'FAILED') AS failed,
               count(*) FILTER (WHERE t.status = 'PENDING') AS pending,
               coalesce(sum(t.amount) FILTER (WHERE t.status IN {SUCCEEDED}), 0) / 100.0 AS succeeded_inr
        FROM raw_payment_txns t JOIN raw_payment_orders o ON o.id = t.order_id
        WHERE t.created_at >= $s AND t.created_at < $e AND {_not_internal('o.user_id', x)}""", {"s": s, "e": e})
    att = (r["succeeded"] or 0) + (r["failed"] or 0)
    ref = one(con, f"""
        SELECT count(*) AS refunds, coalesce(sum(r.amount) FILTER (WHERE r.status <> 'FAILED'), 0) / 100.0 AS refund_inr,
               count(*) FILTER (WHERE r.status = 'FAILED') AS refunds_failed
        FROM raw_refunds r LEFT JOIN raw_payment_orders o ON o.id = r.order_id
        WHERE r.created_at >= $s AND r.created_at < $e AND (o.user_id IS NULL OR {_not_internal('o.user_id', x)})""",
              {"s": s, "e": e})
    return {"attempts": att, "succeeded": r["succeeded"], "failed": r["failed"], "pending": r["pending"],
            "success_pct": round(100 * r["succeeded"] / att, 1) if att else None, "succeeded_inr": round(r["succeeded_inr"] or 0, 2),
            **ref}


@router.get("/payments")
def payments(days: int = 30, exclude_internal: bool = True):
    x = exclude_internal
    with connect() as con:
        w = _window(con, days)
        cur = _pay_numbers(con, w["start"], w["end"], x)
        pw = _prev(w)
        prev = _pay_numbers(con, pw[0], pw[1], x) if pw else None
        p = {"s": w["start"], "e": w["end"]}
        base = f"""FROM raw_payment_txns t JOIN raw_payment_orders o ON o.id = t.order_id
                   WHERE t.created_at >= $s AND t.created_at < $e AND {_not_internal('o.user_id', x)}"""
        by_method = rows(con, f"""
            SELECT coalesce(t.method, 'unknown') AS method, count(*) FILTER (WHERE t.status IN {SUCCEEDED}) AS succeeded,
                   count(*) FILTER (WHERE t.status = 'FAILED') AS failed, count(*) FILTER (WHERE t.status = 'PENDING') AS pending
            {base} GROUP BY 1 ORDER BY 2 + 3 DESC""", p)
        for m in by_method:
            a = m["succeeded"] + m["failed"]
            m["success_pct"] = round(100 * m["succeeded"] / a, 1) if a else None
        reasons = rows(con, f"""
            SELECT coalesce(nullif(trim(t.error_description), ''), 'No reason given') AS reason, t.error_source AS source,
                   count(*) AS attempts
            {base} AND t.status = 'FAILED' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10""", p)
        daily = rows(con, f"""
            SELECT {_ist_day('t.created_at')} AS date, count(*) FILTER (WHERE t.status IN {SUCCEEDED}) AS succeeded,
                   count(*) FILTER (WHERE t.status = 'FAILED') AS failed
            {base} GROUP BY 1 ORDER BY 1""", p)
        by_product = rows(con, f"""
            SELECT lower(coalesce(o.reference_type, 'other')) AS product, count(*) FILTER (WHERE t.status IN {SUCCEEDED}) AS succeeded,
                   count(*) FILTER (WHERE t.status = 'FAILED') AS failed
            {base} GROUP BY 1 ORDER BY 2 DESC""", p)
    return {"numbers": _compare(cur, prev), "has_previous": bool(pw), "by_method": by_method, "reasons": reasons,
            "daily": daily, "by_product": by_product}


@router.get("/list/payments")
def list_payments(days: int = 30, exclude_internal: bool = True, status: str | None = None, method: str | None = None,
                  reason: str | None = None, limit: int = Query(200, ge=1, le=1000)):
    """Drill-down: payment attempts, each with its booking and chat."""
    st = {"succeeded": f"t.status IN {SUCCEEDED}", "failed": "t.status = 'FAILED'", "pending": "t.status = 'PENDING'"}
    if status and status not in st:
        raise HTTPException(400, f"status must be one of {list(st)}")
    with connect() as con:
        w = _window(con, days)
        return {"items": rows(con, f"""
            SELECT t.id, t.razorpay_payment_id, t.status, t.method, t.amount / 100.0 AS inr, t.error_description, t.error_source,
                   t.created_at + INTERVAL '{OFF} minutes' AS created_local, lower(o.reference_type) AS product,
                   o.reference_id AS booking_id, o.user_id, b.chat_session_id, b.status AS booking_status
            FROM raw_payment_txns t JOIN raw_payment_orders o ON o.id = t.order_id
            LEFT JOIN bookings_all b ON b.id = o.reference_id
            WHERE t.created_at >= $s AND t.created_at < $e AND {_not_internal('o.user_id', exclude_internal)}
              AND ({st[status] if status else 'true'}) AND ($m IS NULL OR coalesce(t.method, 'unknown') = $m)
              AND ($r IS NULL OR coalesce(nullif(trim(t.error_description), ''), 'No reason given') = $r)
            ORDER BY t.created_at DESC LIMIT {limit}""", {"s": w["start"], "e": w["end"], "m": method, "r": reason})}


@router.get("/support")
def support(days: int = 30, exclude_internal: bool = True):
    x = exclude_internal
    with connect() as con:
        w = _window(con, days)
        now = w["synced_at"]
        p = {"s": w["start"], "e": w["end"]}
        ni = _not_internal("t.user_id", x)
        opened = one(con, f"""
            SELECT count(*) AS opened, count(*) FILTER (WHERE closed_at IS NOT NULL) AS closed_since,
                   round(CAST(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM (closed_at - created_at)) / 3600)
                         FILTER (WHERE closed_at IS NOT NULL) AS numeric), 1) AS median_hours_to_close
            FROM raw_support_tickets t WHERE created_at >= $s AND created_at < $e AND {ni}""", p)
        by = rows(con, f"""
            SELECT priority, status, count(*) AS tickets FROM raw_support_tickets t
            WHERE created_at >= $s AND created_at < $e AND {ni} GROUP BY 1, 2 ORDER BY 1, 2""", p)
        tickets = rows(con, f"""
            SELECT number, priority, status, source, user_id, created_at + INTERVAL '{OFF} minutes' AS created_local,
                   closed_at + INTERVAL '{OFF} minutes' AS closed_local,
                   round(CAST(extract(epoch FROM (coalesce(closed_at, CAST($now AS timestamp)) - created_at)) / 3600 AS numeric), 1) AS hours_open
            FROM raw_support_tickets t WHERE created_at >= $s AND created_at < $e AND {ni}
            ORDER BY created_at DESC LIMIT 200""", {**p, "now": now})
        return {"awaiting": _support(con, now, x), "opened": opened, "by": by, "tickets": tickets}


# ---------------------------------------------------------------------------------------------------------------
# Checkout steps
# ---------------------------------------------------------------------------------------------------------------

@router.get("/checkout")
def checkout(days: int = 30, exclude_internal: bool = True, product: str = "flight"):
    x = exclude_internal
    with connect() as con:
        w = _window(con, days)
        p = {"s": w["start"], "e": w["end"], "pr": product}
        base = f"""FROM raw_checkout_events c WHERE c.event_at >= $s AND c.event_at < $e AND {_not_internal('c.user_id', x)}"""
        # step order from the data: where each step usually falls within an attempt
        steps = rows(con, f"""
            WITH v AS (SELECT booking_attempt_id, step, min(event_at) AS at {base}
                       AND event_name = 'booking_step_viewed' AND product = $pr AND booking_attempt_id IS NOT NULL GROUP BY 1, 2),
            r AS (SELECT step, booking_attempt_id, rank() OVER (PARTITION BY booking_attempt_id ORDER BY at) AS pos FROM v),
            c AS (SELECT step, count(DISTINCT booking_attempt_id) AS completed {base}
                  AND event_name = 'booking_step_completed' AND product = $pr GROUP BY 1),
            sk AS (SELECT step, count(DISTINCT booking_attempt_id) AS skipped {base}
                   AND event_name = 'booking_step_completed' AND product = $pr AND skipped GROUP BY 1)
            SELECT r.step, count(DISTINCT r.booking_attempt_id) AS viewed, avg(r.pos) AS avg_pos,
                   coalesce(max(c.completed), 0) AS completed, coalesce(max(sk.skipped), 0) AS skipped
            FROM r LEFT JOIN c USING (step) LEFT JOIN sk USING (step) GROUP BY 1 ORDER BY avg(r.pos), 2 DESC""", p)
        ends = one(con, f"""
            SELECT count(DISTINCT booking_attempt_id) FILTER (WHERE event_name = 'booking_step_viewed') AS attempts,
                   count(DISTINCT booking_attempt_id) FILTER (WHERE event_name = 'payment_checkout_opened') AS payment_opened,
                   count(DISTINCT chat_session_id) FILTER (WHERE event_name = 'booking_step_viewed') AS chats
            {base} AND product = $pr""", p)
        confirmed = one(con, f"""
            SELECT count(*) AS confirmed FROM bookings_all b WHERE b.product = $pr AND b.confirmed
              AND b.created_at >= $s AND b.created_at < $e AND (NOT $x OR NOT b.is_internal)""", {**p, "x": x})
        daily = rows(con, f"""
            SELECT {_ist_day('c.event_at')} AS date, count(DISTINCT booking_attempt_id) FILTER (WHERE event_name = 'booking_step_viewed') AS attempts,
                   count(DISTINCT booking_attempt_id) FILTER (WHERE event_name = 'payment_checkout_opened') AS payment_opened
            {base} AND product = $pr GROUP BY 1 ORDER BY 1""", p)
    return {"product": product, "steps": steps, "attempts": ends["attempts"], "payment_opened": ends["payment_opened"],
            "chats": ends["chats"], "confirmed": confirmed["confirmed"], "daily": daily}


@router.get("/list/checkout")
def list_checkout(days: int = 30, exclude_internal: bool = True, product: str = "flight", last_step: str | None = None,
                  limit: int = Query(200, ge=1, le=1000)):
    """Drill-down: checkout attempts, with the last step each reached and its chat."""
    with connect() as con:
        w = _window(con, days)
        return {"items": rows(con, f"""
            WITH a AS (SELECT booking_attempt_id, max(chat_session_id) AS chat_session_id, max(user_id) AS user_id,
                              min(event_at) AS started, max(event_at) AS last_at,
                              bool_or(event_name = 'payment_checkout_opened') AS payment_opened,
                              (array_agg(step ORDER BY event_at DESC) FILTER (WHERE event_name = 'booking_step_viewed'))[1] AS last_step,
                              count(DISTINCT step) FILTER (WHERE event_name = 'booking_step_viewed') AS steps_seen
                       FROM raw_checkout_events c
                       WHERE c.event_at >= $s AND c.event_at < $e AND product = $pr AND booking_attempt_id IS NOT NULL
                         AND {_not_internal('c.user_id', exclude_internal)} GROUP BY 1)
            SELECT booking_attempt_id, chat_session_id, user_id, last_step, steps_seen, payment_opened,
                   started + INTERVAL '{OFF} minutes' AS started_local,
                   round(CAST(extract(epoch FROM (last_at - started)) / 60 AS numeric), 1) AS minutes
            FROM a WHERE ($ls IS NULL OR last_step = $ls) ORDER BY started DESC LIMIT {limit}""",
            {"s": w["start"], "e": w["end"], "pr": product, "ls": last_step})}


# ---------------------------------------------------------------------------------------------------------------
# Growth: signups, vouchers, wallet, traffic sources
# ---------------------------------------------------------------------------------------------------------------

def _growth_numbers(con, s, e, x) -> dict:
    r = one(con, f"""
        SELECT (SELECT count(*) FROM users u WHERE created_at >= $s AND created_at < $e AND NOT is_guest AND deleted_at IS NULL
                  AND (NOT $x OR NOT is_internal)) AS signups,
               (SELECT count(*) FROM users u WHERE created_at >= $s AND created_at < $e AND is_guest) AS guest_accounts,
               (SELECT count(*) FROM raw_wallet_entries e JOIN raw_wallets wl ON wl.id = e.wallet_id
                 WHERE e.kind = 'CREDIT' AND e.reference_type = 'LAUNCH_VOUCHER' AND e.created_at >= $s AND e.created_at < $e
                   AND {_not_internal('wl.user_id', x)}) AS vouchers,
               (SELECT coalesce(sum(e.amount), 0) / 100.0 FROM raw_wallet_entries e JOIN raw_wallets wl ON wl.id = e.wallet_id
                 WHERE e.kind = 'CREDIT' AND e.reference_type = 'LAUNCH_VOUCHER' AND e.created_at >= $s AND e.created_at < $e
                   AND {_not_internal('wl.user_id', x)}) AS voucher_inr,
               (SELECT coalesce(sum(e.amount), 0) / 100.0 FROM raw_wallet_entries e JOIN raw_wallets wl ON wl.id = e.wallet_id
                 WHERE e.kind = 'DEBIT' AND e.status = 'SPENT' AND e.created_at >= $s AND e.created_at < $e
                   AND {_not_internal('wl.user_id', x)}) AS wallet_spent_inr,
               (SELECT count(*) FROM raw_visits v WHERE created_at >= $s AND created_at < $e) AS visits,
               (SELECT count(DISTINCT visitor_id) FROM raw_visits v WHERE created_at >= $s AND created_at < $e) AS visitors""",
            {"s": s, "e": e, "x": x})
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}


@router.get("/growth")
def growth(days: int = 30, exclude_internal: bool = True):
    x = exclude_internal
    with connect() as con:
        w = _window(con, days)
        cur = _growth_numbers(con, w["start"], w["end"], x)
        pw = _prev(w)
        prev = _growth_numbers(con, pw[0], pw[1], x) if pw else None
        p = {"s": w["start"], "e": w["end"], "x": x}
        daily = rows(con, f"""
            SELECT {_ist_day('created_at')} AS date, count(*) FILTER (WHERE NOT is_guest AND deleted_at IS NULL) AS signups,
                   count(*) FILTER (WHERE is_guest) AS guests
            FROM users WHERE created_at >= $s AND created_at < $e AND (NOT $x OR NOT is_internal) GROUP BY 1 ORDER BY 1""", p)
        by_signup_source = rows(con, """
            SELECT coalesce(signup_source, 'unknown') AS source, count(*) AS signups FROM users
            WHERE created_at >= $s AND created_at < $e AND NOT is_guest AND deleted_at IS NULL AND (NOT $x OR NOT is_internal)
            GROUP BY 1 ORDER BY 2 DESC""", p)
        wallet = one(con, f"""
            WITH holders AS (SELECT DISTINCT e.wallet_id FROM raw_wallet_entries e JOIN raw_wallets wl ON wl.id = e.wallet_id
                             WHERE e.kind = 'CREDIT' AND e.reference_type = 'LAUNCH_VOUCHER' AND {_not_internal('wl.user_id', x)})
            SELECT (SELECT coalesce(sum(any_balance + flight_balance + hotel_balance), 0) / 100.0 FROM raw_wallets wl
                     WHERE {_not_internal('wl.user_id', x)}) AS outstanding_inr,
                   (SELECT coalesce(sum(any_balance), 0) / 100.0 FROM raw_wallets wl WHERE {_not_internal('wl.user_id', x)}) AS any_inr,
                   (SELECT coalesce(sum(flight_balance), 0) / 100.0 FROM raw_wallets wl WHERE {_not_internal('wl.user_id', x)}) AS flight_inr,
                   (SELECT coalesce(sum(hotel_balance), 0) / 100.0 FROM raw_wallets wl WHERE {_not_internal('wl.user_id', x)}) AS hotel_inr,
                   (SELECT count(*) FROM raw_wallets wl WHERE any_balance + flight_balance + hotel_balance > 0
                     AND {_not_internal('wl.user_id', x)}) AS wallets_with_balance,
                   (SELECT count(*) FROM holders h WHERE EXISTS (SELECT 1 FROM raw_wallet_entries d WHERE d.wallet_id = h.wallet_id
                     AND d.kind = 'DEBIT' AND d.status = 'SPENT')) AS vouchers_used,
                   (SELECT count(*) FROM holders h WHERE NOT EXISTS (SELECT 1 FROM raw_wallet_entries d WHERE d.wallet_id = h.wallet_id
                     AND d.kind = 'DEBIT' AND d.status = 'SPENT')) AS vouchers_unused""")
        credits = rows(con, f"""
            SELECT e.kind, e.reference_type, e.status, count(*) AS entries, coalesce(sum(e.amount), 0) / 100.0 AS inr
            FROM raw_wallet_entries e JOIN raw_wallets wl ON wl.id = e.wallet_id
            WHERE e.created_at >= $s AND e.created_at < $e AND {_not_internal('wl.user_id', x)}
            GROUP BY 1, 2, 3 ORDER BY 4 DESC""", p)
        sources = rows(con, """
            SELECT source, count(*) AS visits, count(DISTINCT visitor_id) AS visitors FROM raw_visits
            WHERE created_at >= $s AND created_at < $e GROUP BY 1 ORDER BY 2 DESC LIMIT 12""", p)
        referrers = rows(con, """
            SELECT coalesce(nullif(referrer_host, ''), '(none)') AS referrer, count(*) AS visits FROM raw_visits
            WHERE created_at >= $s AND created_at < $e GROUP BY 1 ORDER BY 2 DESC LIMIT 10""", p)
        landings = rows(con, """
            SELECT coalesce(nullif(landing_path, ''), '/') AS landing, count(*) AS visits FROM raw_visits
            WHERE created_at >= $s AND created_at < $e GROUP BY 1 ORDER BY 2 DESC LIMIT 10""", p)
        # signed-in users by the UTM tags of their first attributed visit, and how many of them went on to book
        campaigns = rows(con, f"""
            WITH first AS (SELECT DISTINCT ON (user_id) user_id, coalesce(utm_source, source, '(none)') AS utm_source,
                                  coalesce(utm_medium, '(none)') AS utm_medium, coalesce(utm_campaign, '(none)') AS utm_campaign
                           FROM raw_attribution WHERE user_id IS NOT NULL ORDER BY user_id, event_at),
                 act AS (SELECT DISTINCT a.user_id FROM raw_attribution a WHERE a.event_at >= $s AND a.event_at < $e)
            SELECT f.utm_source, f.utm_medium, f.utm_campaign, count(*) AS users,
                   count(*) FILTER (WHERE EXISTS (SELECT 1 FROM bookings_all b WHERE b.user_id = f.user_id AND b.confirmed)) AS booked
            FROM first f JOIN act USING (user_id) WHERE {_not_internal('f.user_id', x)}
            GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 15""", p)
    return {"numbers": _compare(cur, prev), "has_previous": bool(pw), "daily": daily, "by_signup_source": by_signup_source,
            "wallet": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in (wallet or {}).items()},
            "wallet_entries": credits, "sources": sources, "referrers": referrers, "landings": landings, "campaigns": campaigns}


# ---------------------------------------------------------------------------------------------------------------
# Audience: accounts vs guests, web vs app, DAU / WAU, returning, retained (from per-user-day app activity)
# ---------------------------------------------------------------------------------------------------------------

def _audience(con, sd, ed, x) -> dict:
    g = GUEST.format(c="d.user_id")
    r = one(con, f"""
        SELECT count(DISTINCT d.user_id) AS active, count(DISTINCT d.user_id) FILTER (WHERE NOT {g}) AS accounts,
               count(DISTINCT d.user_id) FILTER (WHERE {g}) AS guests,
               count(DISTINCT d.user_id) FILTER (WHERE d.platform = 'web') AS web,
               count(DISTINCT d.user_id) FILTER (WHERE d.platform = 'app') AS app
        FROM raw_user_days d WHERE d.day >= $sd AND d.day < $ed AND d.events > 0 AND {_not_internal('d.user_id', x)}""",
            {"sd": sd, "ed": ed})
    ret = one(con, f"""
        WITH firsts AS (SELECT user_id, min(day) AS first_day FROM raw_user_days WHERE engaged > 0 GROUP BY 1),
             act AS (SELECT user_id, max(day) AS last_day FROM raw_user_days d
                     WHERE day >= $sd AND day < $ed AND engaged > 0 AND {_not_internal('d.user_id', x)} GROUP BY 1)
        SELECT count(*) AS engaged, count(*) FILTER (WHERE a.last_day > f.first_day) AS came_back
        FROM act a JOIN firsts f USING (user_id)""", {"sd": sd, "ed": ed})
    return {**r, **ret, "came_back_pct": round(100 * ret["came_back"] / ret["engaged"], 1) if ret["engaged"] else None}


@router.get("/audience")
def audience(days: int = 30, exclude_internal: bool = True):
    x = exclude_internal
    with connect() as con:
        w = _window(con, days)
        to_d = lambda t: (t + timedelta(minutes=OFF)).date()  # noqa: E731
        sd, ed = to_d(w["start"]), to_d(w["end"] - timedelta(seconds=1)) + timedelta(days=1)
        cur = _audience(con, sd, ed, x)
        pw = _prev(w)
        psd, ped = (to_d(pw[0]), to_d(pw[1] - timedelta(seconds=1)) + timedelta(days=1)) if pw else (None, None)
        prev = _audience(con, psd, ped, x) if pw else None
        g = GUEST.format(c="d.user_id")
        daily = rows(con, f"""
            SELECT d.day AS date, count(DISTINCT d.user_id) FILTER (WHERE NOT {g}) AS accounts,
                   count(DISTINCT d.user_id) FILTER (WHERE {g}) AS guests,
                   count(DISTINCT d.user_id) FILTER (WHERE d.platform = 'web') AS web,
                   count(DISTINCT d.user_id) FILTER (WHERE d.platform = 'app') AS app
            FROM raw_user_days d WHERE d.day >= $sd AND d.day < $ed AND d.events > 0 AND {_not_internal('d.user_id', x)}
            GROUP BY 1 ORDER BY 1""", {"sd": sd, "ed": ed})
        last = to_d(w["synced_at"])
        live = one(con, f"""
            SELECT count(DISTINCT user_id) FILTER (WHERE day = $t) AS dau_today,
                   count(DISTINCT user_id) FILTER (WHERE day = $t - 1) AS dau_yesterday,
                   count(DISTINCT user_id) FILTER (WHERE day > $t - 7) AS wau,
                   count(DISTINCT user_id) FILTER (WHERE day <= $t - 7 AND day > $t - 14) AS wau_before
            FROM raw_user_days d WHERE day > $t - 14 AND events > 0 AND {_not_internal('d.user_id', x)}""", {"t": last})
        retained = None
        if pw:
            retained = one(con, f"""
                WITH cohort AS (SELECT DISTINCT user_id FROM raw_user_days d WHERE day >= $psd AND day < $ped AND events > 0
                                AND {_not_internal('d.user_id', x)})
                SELECT count(*) AS cohort, count(*) FILTER (WHERE EXISTS (SELECT 1 FROM raw_user_days n WHERE n.user_id = c.user_id
                       AND n.day >= $sd AND n.day < $ed AND n.events > 0)) AS stayed FROM cohort c""",
                           {"psd": psd, "ped": ped, "sd": sd, "ed": ed})
            retained["stayed_pct"] = round(100 * retained["stayed"] / retained["cohort"], 1) if retained["cohort"] else None
    return {"numbers": _compare(cur, prev), "has_previous": bool(pw), "daily": daily, "live": {**live, "as_of": str(last)},
            "retained": retained}


# ---------------------------------------------------------------------------------------------------------------
# Quality: reply ratings, server errors, hotel catalogue sync
# ---------------------------------------------------------------------------------------------------------------

@router.get("/services")
def services(force: bool = False):
    from . import service_checks
    return service_checks.status(force)


def _quality_numbers(con, s, e, x) -> dict:
    r = one(con, f"""
        SELECT (SELECT count(*) FROM raw_feedback f WHERE f.is_positive AND created_at >= $s AND created_at < $e AND {_not_internal('f.user_id', x)}) AS thumbs_up,
               (SELECT count(*) FROM raw_feedback f WHERE NOT f.is_positive AND created_at >= $s AND created_at < $e AND {_not_internal('f.user_id', x)}) AS thumbs_down,
               (SELECT count(*) FROM raw_server_errors WHERE event_name = 'server_error' AND event_at >= $s AND event_at < $e) AS server_errors,
               (SELECT count(*) FROM raw_server_errors WHERE event_name = 'request_rejected' AND event_at >= $s AND event_at < $e) AS rejected""",
            {"s": s, "e": e})
    t = (r["thumbs_up"] or 0) + (r["thumbs_down"] or 0)
    return {**r, "down_pct": round(100 * r["thumbs_down"] / t, 1) if t else None}


@router.get("/quality")
def quality(days: int = 30, exclude_internal: bool = True):
    x = exclude_internal
    with connect() as con:
        w = _window(con, days)
        cur = _quality_numbers(con, w["start"], w["end"], x)
        pw = _prev(w)
        prev = _quality_numbers(con, pw[0], pw[1], x) if pw else None
        p = {"s": w["start"], "e": w["end"]}
        comments = rows(con, f"""
            SELECT f.message_id, f.chat_session_id, f.is_positive, f.comment, f.created_at + INTERVAL '{OFF} minutes' AS created_local
            FROM raw_feedback f WHERE created_at >= $s AND created_at < $e AND {_not_internal('f.user_id', x)}
            ORDER BY created_at DESC LIMIT 100""", p)
        endpoints = rows(con, """
            SELECT event_name, coalesce(method, '?') || ' ' || coalesce(nullif(path, ''), '?') AS endpoint, count(*) AS errors,
                   max(status) AS status, max(error_type) AS error_type, count(DISTINCT user_id) AS users
            FROM raw_server_errors WHERE event_at >= $s AND event_at < $e GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25""", p)
        daily = rows(con, f"""
            SELECT {_ist_day('event_at')} AS date, count(*) FILTER (WHERE event_name = 'server_error') AS server_errors,
                   count(*) FILTER (WHERE event_name = 'request_rejected') AS rejected
            FROM raw_server_errors WHERE event_at >= $s AND event_at < $e GROUP BY 1 ORDER BY 1""", p)
        sync_runs = rows(con, f"""
            SELECT trigger, status, started_at + INTERVAL '{OFF} minutes' AS started_local,
                   finished_at + INTERVAL '{OFF} minutes' AS finished_local, total_hotels, indexed_cities, content_missing_count, error
            FROM raw_hotel_sync_runs ORDER BY started_at DESC LIMIT 15""")
        stale = one(con, """SELECT NOT EXISTS (SELECT 1 FROM raw_hotel_sync_runs WHERE status = 'SUCCEEDED'
                                   AND finished_at >= (SELECT synced_at FROM sync_meta) - INTERVAL '26 hours') AS stale""")
    return {"numbers": _compare(cur, prev), "has_previous": bool(pw), "comments": comments, "endpoints": endpoints,
            "daily": daily, "hotel_sync": {"runs": sync_runs, "stale": (stale or {}).get("stale")}}
