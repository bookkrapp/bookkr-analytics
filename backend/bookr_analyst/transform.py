"""Derived analytics tables, built in Postgres (schema being synced) right after each sync."""
from .config import settings

# Card / panel types that mean "the user was shown bookable search results".
# (itinerary_options / trip_plan_options are AI trip ideas, not search results.)
RESULT_CARDS = ["flight_results", "hotel_card", "hotel_booking_result"]
RESULT_PANELS = ["flight_results", "hotel_results", "multi_city_flights", "multi_city_hotel_results"]

# What the user came to do, one value per chat (session_facts.journey_type). Order matters: first match wins.
#   trip_plan    asked Bookkr to plan a trip (multi-city flow, a plan card or panel, or a trip-planning intent)
#   flight_hotel searched (or asked for) both flights and hotels
#   round_trip   flights only, with a round-trip search or round-trip intent
#   one_way      flights only, one-way
#   hotel        hotels only
#   unclear      no search and no usable intent yet
JOURNEY_SQL = """
    CASE WHEN b.is_trip_plan THEN 'trip_plan'
         WHEN (b.n_flight_searches > 0 OR (b.n_hotel_searches = 0 AND b.has_flight_intent))
              AND (b.n_hotel_searches > 0 OR (b.n_flight_searches = 0 AND b.has_hotel_intent)) THEN 'flight_hotel'
         WHEN b.n_flight_searches > 0 OR (b.n_hotel_searches = 0 AND b.has_flight_intent)
              THEN CASE WHEN b.any_round_trip_search
                          OR (b.n_flight_searches = 0 AND b.intent_trip_type = 'ROUND_TRIP') THEN 'round_trip'
                        ELSE 'one_way' END
         WHEN b.n_hotel_searches > 0 OR b.has_hotel_intent THEN 'hotel'
         ELSE 'unclear' END"""

STAGES = [
    (1, "Chat created"),
    (2, "User engaged"),
    (3, "Intent captured"),
    (4, "Search run"),
    (5, "Results shown"),
    (6, "Booking attempted"),
    (7, "Booked"),
]


def build(con) -> None:
    off = int(settings.tz_offset_minutes)
    _index(con, RAW_INDEXES)
    con.execute(
        """
        CREATE TABLE users AS
        SELECT id AS user_id, email_domain,
               email_domain = 'anonymous.bookkr.local' AS is_guest,
               (id = ANY($ids) OR email_domain = ANY($domains)) AS is_internal,
               signup_source, created_at, last_login_at, deleted_at
        FROM raw_users
        """,
        {"ids": list(settings.internal_user_ids) or [""], "domains": list(settings.internal_email_domains) or [""]},
    )

    _index(con, [("users", "user_id", "INCLUDE (is_internal)")])  # the internal-user filter runs once per row

    # a view, not a copy: messages are the biggest table and the database is billed by size
    con.execute(f"""
        CREATE VIEW messages AS
        SELECT *, created_at + INTERVAL '{off} minutes' AS created_local
        FROM raw_messages WHERE deleted_at IS NULL
    """)

    con.execute(f"""
        CREATE TABLE session_facts AS
        WITH m AS (
            SELECT chat_session_id,
                   count(*) AS n_messages,
                   count(*) FILTER (WHERE role = 'USER') AS n_user_msgs,
                   count(*) FILTER (WHERE role = 'ASSISTANT') AS n_assistant_msgs,
                   min(created_at) AS first_msg_at, max(created_at) AS last_msg_at,
                   bool_or(card_type = 'intent_confirmation') AS has_intent_card,
                   bool_or(card_type = ANY($result_cards)) AS has_result_card,
                   bool_or(card_type = 'payment_result') AS has_payment_card,
                   bool_or(card_type = 'itinerary_options') AS has_plan_card,
                   bool_or(card_type = 'itinerary_dates') AS has_plan_dates_card,
                   count(*) FILTER (WHERE kind = 'shared_failure') AS n_failure_msgs
            FROM messages GROUP BY 1),
        fs AS (SELECT chat_session_id, count(*) AS n_flight_searches, min(created_at) AS first_fs_at,
                      bool_or(is_round_trip) AS any_round_trip_search
               FROM raw_flight_searches GROUP BY 1),
        hs AS (SELECT chat_session_id, count(*) AS n_hotel_searches, min(created_at) AS first_hs_at
               FROM raw_hotel_searches GROUP BY 1),
        fb AS (SELECT chat_session_id, count(*) AS n_booking_attempts,
                      count(*) FILTER (WHERE status = 'BOOKED') AS n_booked,
                      coalesce(sum(amount_payable) FILTER (WHERE status = 'BOOKED'), 0) AS gmv,
                      count(*) FILTER (WHERE status = 'FAILED') AS n_failed_bookings
               FROM raw_flight_bookings WHERE chat_session_id IS NOT NULL GROUP BY 1),
        it AS (SELECT chat_session_id, count(*) AS n_intents, avg(confidence) AS avg_intent_confidence,
                      bool_or(intent_type = 'hotel_search') AS has_hotel_intent,
                      bool_or(intent_type = 'flight_search'
                              OR (intent_type = 'travel_intent' AND parameters->>'origin' IS NOT NULL
                                  AND parameters->>'destination' IS NOT NULL)) AS has_flight_intent,
                      bool_or(intent_type = 'multi_city_trip_planning') AS has_plan_intent
               FROM raw_intents WHERE deleted_at IS NULL GROUP BY 1),
        llm AS (SELECT chat_session_id, sum(cost_usd) AS llm_cost_usd, count(*) AS n_llm_calls
                FROM raw_llm_calls GROUP BY 1),
        base AS (
            SELECT s.id AS session_id, s.user_id, coalesce(u.is_internal, false) AS is_internal,
                   coalesce(u.is_guest, false) AS is_guest, s.title, s.chat_type, s.is_group_chat,
                   s.created_at, s.created_at + INTERVAL '{off} minutes' AS created_local, s.updated_at,
                   coalesce(f.current_flow, 'none') AS current_flow,
                   f.flight_state->>'status' AS flight_state_status,
                   f.multi_city_state->>'phase' AS multi_city_phase,
                   coalesce(m.n_messages, 0) AS n_messages, coalesce(m.n_user_msgs, 0) AS n_user_msgs,
                   coalesce(m.n_assistant_msgs, 0) AS n_assistant_msgs,
                   m.first_msg_at, m.last_msg_at,
                   coalesce(m.has_intent_card, false) AS has_intent_card,
                   coalesce(m.has_result_card, false)
                     OR EXISTS (SELECT 1 FROM json_array_elements_text(coalesce(s.panel_card_types, '[]'::json)) pc(t)
                                WHERE pc.t = ANY($result_panels))
                     AS has_results,
                   coalesce(m.has_payment_card, false) AS has_payment_card,
                   coalesce(m.n_failure_msgs, 0) AS n_failure_msgs,
                   coalesce(m.has_plan_card, false)
                     OR coalesce(m.has_plan_dates_card, false)
                     OR coalesce(it.has_plan_intent, false)
                     OR coalesce(f.current_flow = 'multi_city', false)
                     OR EXISTS (SELECT 1 FROM json_array_elements_text(coalesce(s.panel_card_types, '[]'::json)) pc(t)
                                WHERE pc.t = 'trip_plan_options')
                     AS is_trip_plan,
                   -- trip-planning steps: plan ideas shown, then dates picked for a chosen plan
                   coalesce(m.has_plan_card, false)
                     OR EXISTS (SELECT 1 FROM json_array_elements_text(coalesce(s.panel_card_types, '[]'::json)) pc(t)
                                WHERE pc.t = 'trip_plan_options')
                     AS plan_shown,
                   coalesce(m.has_plan_dates_card, false)
                     OR coalesce(f.multi_city_state->>'phase' IN ('dates_generated', 'flights_shown'), false)
                     AS plan_dates_picked,
                   coalesce(fs.any_round_trip_search, false) AS any_round_trip_search,
                   coalesce(it.has_hotel_intent, false) AS has_hotel_intent,
                   coalesce(it.has_flight_intent, false) AS has_flight_intent,
                   f.travel_intent->>'tripType' AS intent_trip_type,
                   coalesce(fs.n_flight_searches, 0) AS n_flight_searches,
                   coalesce(hs.n_hotel_searches, 0) AS n_hotel_searches,
                   least(fs.first_fs_at, hs.first_hs_at) AS first_search_at,
                   coalesce(fb.n_booking_attempts, 0) AS n_booking_attempts,
                   coalesce(fb.n_booked, 0) AS n_booked, coalesce(fb.gmv, 0) AS gmv,
                   coalesce(fb.n_failed_bookings, 0) AS n_failed_bookings,
                   coalesce(it.n_intents, 0) AS n_intents, it.avg_intent_confidence,
                   coalesce(llm.llm_cost_usd, 0) AS llm_cost_usd, coalesce(llm.n_llm_calls, 0) AS n_llm_calls
            FROM raw_chat_sessions s
            LEFT JOIN users u ON u.user_id = s.user_id
            LEFT JOIN raw_flow_state f ON f.chat_session_id = s.id
            LEFT JOIN m ON m.chat_session_id = s.id
            LEFT JOIN fs ON fs.chat_session_id = s.id
            LEFT JOIN hs ON hs.chat_session_id = s.id
            LEFT JOIN fb ON fb.chat_session_id = s.id
            LEFT JOIN it ON it.chat_session_id = s.id
            LEFT JOIN llm ON llm.chat_session_id = s.id)
        SELECT b.*,
               {JOURNEY_SQL} AS journey_type,
               CASE WHEN n_booked > 0 THEN 7
                    WHEN n_booking_attempts > 0 THEN 6
                    WHEN has_results THEN 5
                    WHEN n_flight_searches + n_hotel_searches > 0 THEN 4
                    WHEN has_intent_card OR flight_state_status = 'intent_ready' OR multi_city_phase IS NOT NULL THEN 3
                    WHEN n_user_msgs > 0 THEN 2 ELSE 1 END AS stage_level,
               (extract(epoch FROM date_trunc('second', first_search_at))
                 - extract(epoch FROM date_trunc('second', first_msg_at))) / 60.0 AS minutes_to_first_search,
               (SELECT count(*) FROM messages mm
                 WHERE mm.chat_session_id = b.session_id AND mm.role = 'USER'
                   AND (b.first_search_at IS NULL OR mm.created_at < b.first_search_at)) AS user_msgs_before_search
        FROM base b
    """, {"result_cards": RESULT_CARDS, "result_panels": RESULT_PANELS})

    con.execute("CREATE TABLE stages (level INTEGER, name VARCHAR)")
    con.executemany("INSERT INTO stages VALUES (?, ?)", STAGES)

    con.execute(f"""
        CREATE TABLE booking_ops AS
        WITH meta AS (SELECT synced_at FROM sync_meta LIMIT 1)
        SELECT b.*, coalesce(u.is_internal, false) AS is_internal,
               b.created_at + INTERVAL '{off} minutes' AS created_local,
               round((extract(epoch FROM date_trunc('minute', meta.synced_at))
                      - extract(epoch FROM date_trunc('minute', b.updated_at))) / 3600.0, 1) AS hours_since_update,
               CASE
                 WHEN b.status = 'FAILED' AND b.payment_status IN ('CAPTURED','VOID_PENDING','CAPTURED_AWAITING_PROVIDER') THEN 'critical'
                 WHEN b.status IN ('PENDING','PENDING_BOOK','PENDING_PAYMENT')
                      AND b.payment_status IN ('CAPTURED','CAPTURED_AWAITING_PROVIDER') THEN 'critical'
                 WHEN b.status IN ('PENDING','PENDING_BOOK','PENDING_PAYMENT')
                      AND b.updated_at < meta.synced_at - INTERVAL '30 minutes' THEN 'warning'
                 WHEN b.status = 'BOOKED' AND b.confirmation_email_sent_at IS NULL THEN 'warning'
                 WHEN b.status = 'FAILED' THEN 'info'
                 ELSE 'ok' END AS severity,
               CASE
                 WHEN b.status = 'FAILED' AND b.payment_status = 'VOID_PENDING' THEN 'Payment taken, booking failed – void/refund pending'
                 WHEN b.status = 'FAILED' AND b.payment_status = 'CAPTURED' THEN 'Payment captured, booking failed'
                 WHEN b.payment_status = 'CAPTURED_AWAITING_PROVIDER' THEN 'Payment captured, waiting on supplier'
                 WHEN b.status IN ('PENDING','PENDING_BOOK','PENDING_PAYMENT') THEN 'Booking stuck in ' || b.status
                 WHEN b.status = 'BOOKED' AND b.confirmation_email_sent_at IS NULL THEN 'Booked, confirmation email not sent'
                 WHEN b.status = 'FAILED' AND b.payment_status = 'VOIDED' THEN 'Failed, payment voided (resolved)'
                 WHEN b.status = 'FAILED' THEN 'Failed before payment'
                 ELSE 'OK' END AS issue,
               CASE
                 WHEN b.failure_reason IS NULL THEN NULL
                 WHEN lower(b.failure_reason) LIKE '%abandon%' THEN 'User abandoned payment'
                 WHEN lower(b.failure_reason) LIKE '%gateway proxy%' OR lower(b.failure_reason) LIKE '%tripjack%' THEN 'Supplier API error'
                 WHEN lower(b.failure_reason) LIKE '%airline%' THEN 'Airline rejected'
                 ELSE 'Other' END AS failure_category
        FROM raw_flight_bookings b
        CROSS JOIN meta
        LEFT JOIN users u ON u.user_id = b.user_id
    """)
    _index(con, DERIVED_INDEXES)


def _index(con, indexes) -> None:
    for table, cols, *rest in indexes:
        con.execute(f"CREATE INDEX ON {table} ({cols}) {rest[0] if rest else ''}")
    for table in sorted({i[0] for i in indexes}):
        con.execute(f"ANALYZE {table}")


# Postgres needs indexes for the per-chat and per-user lookups the dashboards make.
RAW_INDEXES = [
    ("raw_users", "id"), ("raw_chat_sessions", "id"),
    ("raw_messages", "chat_session_id, created_at"), ("raw_messages", "created_at"), ("raw_messages", "user_id"),
    # user messages by time, readable from the index alone (KPI counts)
    ("raw_messages", "created_at", "INCLUDE (user_id, chat_session_id) WHERE deleted_at IS NULL AND role = 'USER'"),
    ("raw_flight_bookings", "chat_session_id"), ("raw_flight_bookings", "user_id"),
    ("raw_flight_searches", "chat_session_id"), ("raw_flight_searches", "user_id"), ("raw_flight_searches", "created_at"),
    ("raw_hotel_searches", "chat_session_id"), ("raw_hotel_searches", "user_id"), ("raw_hotel_searches", "created_at"),
    ("raw_flight_search_results", "search_request_id"), ("raw_hotel_search_results", "search_request_id"),
    ("raw_intents", "chat_session_id"), ("raw_flow_state", "chat_session_id"), ("raw_feedback", "chat_session_id"),
    ("raw_llm_calls", "chat_session_id"), ("raw_llm_calls", "created_at"),
    ("raw_analytics_events", "chat_session_id"), ("raw_analytics_events", "user_id, event_at"),
]
DERIVED_INDEXES = [
    ("session_facts", "session_id"), ("session_facts", "user_id"), ("session_facts", "created_at"),
    ("session_facts", "journey_type, created_at"),
    ("booking_ops", "chat_session_id"), ("booking_ops", "user_id"), ("booking_ops", "created_at"),
]
