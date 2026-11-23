"""Copy masked data from the source Neon DB into Bookr Analyst's own Postgres database.

Run:  python -m bookr_analyst.sync

* The source connection is forced READ ONLY and runs in one REPEATABLE READ
  transaction (consistent snapshot across tables).
* Only the columns listed below are copied; PII columns are dropped, free text
  and JSON are regex-masked (see masking.py).
* Everything is written to a build schema inside ONE transaction on the target and
  swapped in for the `analyst` schema when it commits, so the dashboard never sees
  a half-written copy. The Friction Finder's `friction` schema is not touched.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse

import psycopg

from .config import settings
from .db import ANALYTICS_SCHEMA, Con, open_connection
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


BUILD_SCHEMA = f"{ANALYTICS_SCHEMA}_build"
SYNC_LOCK = 4_815_162_342  # advisory lock: one sync at a time, across every machine sharing the database
CHUNK = 5_000


def create_table(tgt: psycopg.Connection, name: str, columns: list[tuple[str, str]]) -> None:
    tgt.execute(f"CREATE TABLE {name} ({', '.join(f'{c} {t}' for c, t in columns)})")


def copy_rows(tgt: psycopg.Connection, name: str, columns: list[str], rows: Iterable[tuple]) -> int:
    n = 0
    with tgt.cursor().copy(f"COPY {name} ({', '.join(columns)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
            n += 1
    return n


def _target_type(src: psycopg.Connection, oid: int) -> str:
    info = src.adapters.types.get(oid)
    name = info.name if info else "text"
    return {"timestamptz": "timestamp", "jsonb": "json", "name": "text", "bpchar": "text"}.get(name, name)


def _extract(src: psycopg.Connection, tgt: psycopg.Connection, name: str, sql: str, text_cols: list[str],
             json_cols: list[str], since_col: str | None = None) -> int:
    """Stream one source query into the target table `name`, masking on the way. Returns the row count."""
    since = settings.sync_since_utc
    with src.cursor(name=f"x_{name}") as cur:  # server-side cursor: rows are streamed, not held in memory
        if since and since_col:
            cur.execute(f"select * from ({sql}) s where s.{since_col} >= %s", [since])
        else:
            cur.execute(sql)
        cols = [d.name for d in cur.description]
        types = [("json" if d.name in json_cols else _target_type(src, d.type_code)) for d in cur.description]
        create_table(tgt, name, list(zip(cols, types)))
        text_i = [cols.index(c) for c in text_cols]
        json_i = [cols.index(c) for c in json_cols]

        def clean():
            while True:
                chunk = cur.fetchmany(CHUNK)
                if not chunk:
                    return
                for row in chunk:
                    row = list(row)
                    for i in text_i:
                        row[i] = mask_text(row[i])
                    for i in json_i:
                        row[i] = mask_json(row[i])
                    for i, v in enumerate(row):
                        if isinstance(v, datetime) and v.tzinfo is not None:  # store every timestamp as naive UTC
                            row[i] = v.astimezone(timezone.utc).replace(tzinfo=None)
                        elif isinstance(v, (dict, list)):
                            row[i] = json.dumps(v, ensure_ascii=False, default=str)
                    yield row

        return copy_rows(tgt, name, cols, clean())


def begin_build(tgt: psycopg.Connection) -> None:
    """Start the build inside the current transaction of `tgt`."""
    tgt.execute("SELECT pg_advisory_xact_lock(%s)", (SYNC_LOCK,))
    tgt.execute(f"DROP SCHEMA IF EXISTS {BUILD_SCHEMA} CASCADE")
    tgt.execute(f"CREATE SCHEMA {BUILD_SCHEMA}")
    tgt.execute(f"SET LOCAL search_path TO {BUILD_SCHEMA}")


def finish_build(tgt: psycopg.Connection, synced_at: datetime, host: str, counts: dict[str, int], started: float) -> None:
    """Record the sync, build the derived tables and swap the build schema in for the live one."""
    tgt.execute("CREATE TABLE sync_meta (synced_at timestamp, source_host text, row_counts json, duration_s float8)")
    tgt.execute("INSERT INTO sync_meta VALUES (%s, %s, %s, %s)",
                (synced_at, host, json.dumps(counts), round(time.time() - started, 2)))
    transform.build(Con(tgt))
    tgt.execute(f"DROP SCHEMA IF EXISTS {ANALYTICS_SCHEMA} CASCADE")
    tgt.execute(f"ALTER SCHEMA {BUILD_SCHEMA} RENAME TO {ANALYTICS_SCHEMA}")


def run_sync() -> dict:
    if not settings.source_database_url:
        raise RuntimeError("SOURCE_DATABASE_URL is not set in .env")
    started = time.time()
    counts: dict[str, int] = {}
    with open_connection() as tgt:  # one transaction: committed on success, rolled back on any error
        begin_build(tgt)
        with psycopg.connect(settings.source_database_url, autocommit=False) as src:
            src.read_only = True
            src.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            with src.transaction():
                src.execute("SET LOCAL statement_timeout = '300s'")
                for name, (sql, text_cols, json_cols) in EXTRACTS.items():
                    since_col = SINCE_COLUMN.get(name)
                    if name not in OPTIONAL:
                        counts[name] = _extract(src, tgt, name, sql, text_cols, json_cols, since_col)
                        continue
                    try:
                        with src.transaction(), tgt.transaction():  # savepoints: a missing table must not abort the snapshot
                            counts[name] = _extract(src, tgt, name, sql, text_cols, json_cols, since_col)
                    except psycopg.errors.UndefinedTable:
                        create_table(tgt, name, [(c, "timestamp" if c == "event_at" else "json" if c in json_cols else "text")
                                                 for c in OPTIONAL[name]])
                        counts[name] = 0
        synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
        host = urlparse(settings.source_database_url).hostname or ""
        finish_build(tgt, synced_at, host, counts, started)

    return {"synced_at": synced_at.isoformat(), "row_counts": counts, "duration_s": round(time.time() - started, 2)}


if __name__ == "__main__":
    result = run_sync()
    print(f"Synced in {result['duration_s']}s → schema {ANALYTICS_SCHEMA}")
    for k, v in result["row_counts"].items():
        print(f"  {k:28s} {v:>8,}")
