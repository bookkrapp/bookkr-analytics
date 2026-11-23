"""Copy masked data from the source Neon DB into the local DuckDB analytics DB.

Run:  python -m bookr_analyst.sync

* The source connection is forced READ ONLY and runs in one REPEATABLE READ
  transaction (consistent snapshot across tables).
* Only the columns listed below are copied; PII columns are dropped, free text
  and JSON are regex-masked (see masking.py).
* Data is written to a temp file and swapped in at the end, so the dashboard
  never sees a half-written database.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import duckdb
import pandas as pd
import psycopg

from .config import settings
from .masking import mask_json, mask_text
from . import transform

# name -> (source SQL, text columns to mask, json columns to mask)
EXTRACTS: dict[str, tuple[str, list[str], list[str]]] = {
    "raw_users": ("""
        select id, split_part(lower(coalesce(email,'')),'@',2) as email_domain,
               "signupSource" as signup_source, "createdAt" as created_at,
               "lastLoginAt" as last_login_at, "deletedAt" as deleted_at,
               "privacyAcceptedAt" as privacy_accepted_at
        from "User" """, [], []),

    "raw_chat_sessions": ("""
        select id, "userId" as user_id, title, "isActive" as is_active, "chatType" as chat_type,
               "isGroupChat" as is_group_chat, "preferenceVersion" as preference_version,
               "createdAt" as created_at, "updatedAt" as updated_at,
               (latitude is not null) as has_location,
               (select coalesce(jsonb_agg(e->>'type'), '[]'::jsonb)
                  from jsonb_array_elements(case when jsonb_typeof("panelCards")='array'
                                                 then "panelCards" else '[]'::jsonb end) e) as panel_card_types
        from "ChatSession" """, ["title"], ["panel_card_types"]),

    "raw_messages": ("""
        select id, "chatSessionId" as chat_session_id, "userId" as user_id, role::text as role,
               content, kind, "createdAt" as created_at, "deletedAt" as deleted_at,
               metadata->'card'->>'type' as card_type,
               coalesce(metadata->'card'->'superseded' = 'true'::jsonb, false) as card_superseded,
               case when metadata->'card'->>'type' in
                    ('intent_confirmation','payment_result','hotel_booking_result','command_card')
                    then metadata->'card'->'data' end as card_data,
               (select jsonb_object_agg(k, case jsonb_typeof(v)
                          when 'array' then to_jsonb(jsonb_array_length(v))
                          when 'object' then '"{…}"'::jsonb else v end)
                  from jsonb_each(case when jsonb_typeof(metadata->'card'->'data')='object'
                                       then metadata->'card'->'data' else '{}'::jsonb end) kv(k, v)) as card_shape,
               coalesce(metadata->'bookkrInvoked' = 'true'::jsonb, false) as bookkr_invoked,
               metadata - 'card' - 'hotelCard' - 'citations' as meta_rest
        from "Message" """, ["content"], ["card_data", "card_shape", "meta_rest"]),

    "raw_intents": ("""
        select id, "messageId" as message_id, "chatSessionId" as chat_session_id,
               "intentType"::text as intent_type, confidence, parameters,
               "createdAt" as created_at, "deletedAt" as deleted_at
        from "ExtractedIntent" """, [], ["parameters"]),

    "raw_flow_state": ("""
        select "chatSessionId" as chat_session_id, "currentFlow"::text as current_flow,
               "flightState" as flight_state, "multiCityState" as multi_city_state,
               "travelIntent" as travel_intent, "createdAt" as created_at, "updatedAt" as updated_at
        from "UnifiedChatFlowState" """, [], ["flight_state", "multi_city_state", "travel_intent"]),

    "raw_flight_searches": ("""
        select id, "chatSessionId" as chat_session_id, "userId" as user_id,
               "originCode" as origin, "destinationCode" as destination,
               "departureDate" as departure_date, "returnDate" as return_date,
               adults, children, infants, "cabinClass"::text as cabin_class,
               "isRoundTrip" as is_round_trip, "searchHash" as search_hash, "createdAt" as created_at
        from "FlightSearchRequest" """, [], []),

    "raw_flight_search_results": ("""
        select r.id, r."searchRequestId" as search_request_id, r."createdAt" as created_at,
               (select count(*) from "FlightOption" o where o."searchResultId" = r.id) as option_count
        from "FlightSearchResult" r """, [], []),

    "raw_hotel_searches": ("""
        select id, "chatSessionId" as chat_session_id, "userId" as user_id,
               "destinationName" as destination_name, "destinationLat" as destination_lat,
               "destinationLng" as destination_lng, "checkInDate" as check_in_date,
               "checkOutDate" as check_out_date, adults, children, filters,
               "searchHash" as search_hash, "createdAt" as created_at
        from "HotelSearchRequest" """, [], ["filters"]),

    "raw_hotel_search_results": ("""
        select id, "searchRequestId" as search_request_id, provider,
               "rawResponse"->>'total' as total_text,
               case when jsonb_typeof("rawResponse"->'hotels')='array'
                    then jsonb_array_length("rawResponse"->'hotels') else 0 end as hotel_count,
               "retainedUntil" as retained_until, "createdAt" as created_at
        from "HotelSearchResult" """, [], []),

    "raw_flight_bookings": ("""
        select id, "userId" as user_id, "chatSessionId" as chat_session_id, provider,
               "providerBookingId" as provider_booking_id,
               (pnr is not null and pnr <> '') as has_pnr,
               coalesce(cardinality("ticketNumbers"), 0) as ticket_count,
               status::text as status, "paymentStatus" as payment_status,
               "amountPayable"::float8 as amount_payable, "walletAmount" as wallet_amount, currency,
               "failureReason" as failure_reason,
               "createdAt" as created_at, "updatedAt" as updated_at, "ticketedAt" as ticketed_at,
               "cancelledAt" as cancelled_at, "confirmationEmailSentAt" as confirmation_email_sent_at,
               ("paymentOrderId" is not null) as has_payment_order,
               "fareSnapshotJson"->>'fareName' as fare_name,
               "fareSnapshotJson"->>'isLcc' as is_lcc,
               "fareSnapshotJson"->'tripInfo'->0->>'from' as origin,
               "fareSnapshotJson"->'tripInfo'->-1->>'to' as destination,
               "fareSnapshotJson"->'tripInfo'->0->>'airline' as airline,
               "fareSnapshotJson"->'tripInfo'->0->>'departureTime' as departure_time,
               case when jsonb_typeof("fareSnapshotJson"->'tripInfo')='array'
                    then jsonb_array_length("fareSnapshotJson"->'tripInfo') end as leg_count,
               "conditionsJson"->>'international' as international,
               case when jsonb_typeof("bookRequestJson"->'travellers')='array'
                    then jsonb_array_length("bookRequestJson"->'travellers') end as traveller_count,
               coalesce(cardinality("visibleToUserIds"), 0) as visible_to_count,
               "pricingRules"->'platformFee' as platform_fee
        from "FlightBooking" """, ["failure_reason"], ["platform_fee"]),

    "raw_llm_calls": ("""
        select id, task, model, "inputTokens" as input_tokens, "outputTokens" as output_tokens,
               "totalTokens" as total_tokens, "latencyMs" as latency_ms, "costUsd" as cost_usd,
               success, left(error, 500) as error, "userId" as user_id,
               "chatSessionId" as chat_session_id, "createdAt" as created_at
        from "LlmCallLog" """, ["error"], []),

    "raw_feedback": ("""
        select id, "messageId" as message_id, "chatSessionId" as chat_session_id,
               "userId" as user_id, "isPositive" as is_positive, comment, "createdAt" as created_at
        from "AssistantMessageFeedback" """, ["comment"], []),

    # Friction Finder R8: technical and suggestion signals. Only these event names; properties are masked.
    "raw_analytics_events": ("""
        select id, "eventName" as event_name, "userId" as user_id, "chatSessionId" as chat_session_id,
               "messageId" as message_id, "eventAt" as event_at, source, platform, "appVersion" as app_version,
               properties
        from "AnalyticsEvent"
        where "eventName" in ('payment_failed', 'payment_dismissed', 'booking_price_changed', 'booking_fare_expired',
                              'unsupported_feature_requested', 'hotel_search_results_shown',
                              'flight_search_results_shown', 'feature_used')
          and ("eventName" <> 'feature_used'
               or properties->>'feature' in ('reply_chips', 'flight_filters', 'flight_sort', 'hotel_filters', 'hotel_sort'))
        """, [], ["properties"]),
}
# SYNC_SINCE (.env): only rows whose time column is on/after it are copied. Users are always copied in full
# (they are a small lookup table). Chats and bookings use updated_at so older ones with new activity come along.
SINCE_COLUMN = {
    "raw_chat_sessions": "updated_at", "raw_messages": "created_at", "raw_intents": "created_at",
    "raw_flow_state": "updated_at", "raw_flight_searches": "created_at", "raw_flight_search_results": "created_at",
    "raw_hotel_searches": "created_at", "raw_hotel_search_results": "created_at", "raw_flight_bookings": "updated_at",
    "raw_llm_calls": "created_at", "raw_feedback": "created_at", "raw_analytics_events": "event_at",
}
# Tables that may not exist in every source database (older schema): an empty table is created instead.
OPTIONAL = {"raw_analytics_events": ["id", "event_name", "user_id", "chat_session_id", "message_id", "event_at",
                                     "source", "platform", "app_version", "properties"]}


def _extract(conn: psycopg.Connection, sql: str, text_cols: list[str], json_cols: list[str],
             since_col: str | None = None) -> pd.DataFrame:
    since = settings.sync_since_utc
    if since and since_col:
        cur = conn.execute(f"select * from ({sql}) s where s.{since_col} >= %s", [since])
    else:
        cur = conn.execute(sql)
    cols = [d.name for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    for c in text_cols:
        df[c] = df[c].map(mask_text)
    for c in json_cols:
        df[c] = df[c].map(mask_json)
    return df


def _replace_file(src: str, dst: str, attempts: int = 20) -> None:
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:  # Windows: file briefly open by a reader
            time.sleep(0.25)
    raise RuntimeError(f"Could not replace {dst}; is another process holding it open?")


def run_sync(swap_lock=None) -> dict:
    if not settings.source_database_url:
        raise RuntimeError("SOURCE_DATABASE_URL is not set in .env")
    started = time.time()
    final_path = settings.analytics_db_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_suffix(".tmp.duckdb")
    for p in (tmp_path, tmp_path.with_suffix(tmp_path.suffix + ".wal")):
        if p.exists():
            p.unlink()

    frames: dict[str, pd.DataFrame] = {}
    with psycopg.connect(settings.source_database_url, autocommit=False) as conn:
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with conn.transaction():
            conn.execute("SET LOCAL statement_timeout = '300s'")
            for name, (sql, text_cols, json_cols) in EXTRACTS.items():
                since_col = SINCE_COLUMN.get(name)
                if name not in OPTIONAL:
                    frames[name] = _extract(conn, sql, text_cols, json_cols, since_col)
                    continue
                try:
                    with conn.transaction():  # savepoint: a missing table must not abort the snapshot
                        frames[name] = _extract(conn, sql, text_cols, json_cols, since_col)
                except psycopg.errors.UndefinedTable:
                    frames[name] = pd.DataFrame(columns=OPTIONAL[name])

    con = duckdb.connect(str(tmp_path))
    try:
        for name, df in frames.items():
            json_cols = EXTRACTS[name][2]
            con.register("df_in", df)
            if json_cols and len(df):
                replace = ", ".join(f"CAST({c} AS JSON) AS {c}" for c in json_cols)
                con.execute(f"CREATE TABLE {name} AS SELECT * REPLACE ({replace}) FROM df_in")
            elif name in OPTIONAL and not len(df):
                cols = ", ".join(f"{c} {'TIMESTAMP' if c == 'event_at' else 'JSON' if c in json_cols else 'VARCHAR'}"
                                 for c in OPTIONAL[name])
                con.execute(f"CREATE TABLE {name} ({cols})")
            else:
                con.execute(f"CREATE TABLE {name} AS SELECT * FROM df_in")
            con.unregister("df_in")

        synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
        host = urlparse(settings.source_database_url).hostname or ""
        counts = {n: int(len(df)) for n, df in frames.items()}
        con.execute("CREATE TABLE sync_meta (synced_at TIMESTAMP, source_host VARCHAR, row_counts JSON, duration_s DOUBLE)")
        con.execute("INSERT INTO sync_meta VALUES (?, ?, ?, ?)",
                    [synced_at, host, pd.Series(counts).to_json(), round(time.time() - started, 2)])
        transform.build(con)
        con.execute("CHECKPOINT")
    finally:
        con.close()

    if swap_lock is not None:
        with swap_lock:
            _replace_file(str(tmp_path), str(final_path))
    else:
        _replace_file(str(tmp_path), str(final_path))

    return {"synced_at": synced_at.isoformat(), "row_counts": counts, "duration_s": round(time.time() - started, 2)}


if __name__ == "__main__":
    result = run_sync()
    print(f"Synced in {result['duration_s']}s → {settings.analytics_db_path}")
    for k, v in result["row_counts"].items():
        print(f"  {k:28s} {v:>8,}")
