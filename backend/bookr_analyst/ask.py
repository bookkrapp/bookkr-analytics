"""Ask (phase 2): a plain-English question -> one read-only SQL query on the masked analytics copy -> an answer.

1. Claude writes the SQL from the live table list plus the notes below (same account as the Friction Finder judge:
   ANTHROPIC_API_KEY or the Claude Code subscription).
2. The SQL is checked here (one SELECT / WITH statement, analyst tables only) and run in a READ ONLY transaction
   with a statement timeout and a row cap. If Postgres rejects it, Claude gets the error once to fix it.
3. Claude explains the rows it got back. Numbers in the answer come only from those rows.

Every question is logged in friction.ask_log (the persistent schema; the analyst schema is replaced by each sync).
"""
from __future__ import annotations

import json
import re
import threading

from .config import settings
from .db import ANALYTICS_SCHEMA, _clean, session
from .friction import judge as J
from .friction.store import conn as store_conn, now_iso

MAX_ROWS = 500
TIMEOUT = "20s"
LOG_ROWS = 200   # rows kept in the log so a past answer can be shown again without re-running it

NOTES = f"""Key facts about the data (Bookkr: an AI travel-booking chat app in India; flights and hotels):
- All timestamps are UTC. Columns ending in _local are IST (UTC + {settings.tz_offset_minutes} minutes); use them for
  days, weeks and "today". For other tables convert with  created_at + INTERVAL '{settings.tz_offset_minutes} minutes'.
- Money is INR. Booked = raw_flight_bookings.status = 'BOOKED'; GMV = sum(amount_payable) of booked rows.
  Hotel bookings are NOT in the data yet; say so if asked.
- Internal/test users: users.is_internal. session_facts.is_internal and booking_ops.is_internal are copies.
  Unless the question says otherwise, EXCLUDE internal users.
- session_facts: one row per chat (session_id). Useful columns: user_id, created_at, created_local, journey_type,
  stage_level (1 Chat created, 2 User engaged, 3 Intent captured, 4 Search run, 5 Results shown, 6 Booking attempted,
  7 Booked; table `stages` has the names), n_user_msgs, n_flight_searches, n_hotel_searches, n_booking_attempts,
  n_booked, gmv, current_flow, is_trip_plan, plan_shown, plan_dates_picked, llm_cost_usd, is_guest.
- journey_type values: one_way, round_trip, flight_hotel, hotel, trip_plan (asked Bookkr to plan a trip), unclear.
- booking_ops: one row per flight booking attempt with status, payment_status, amount_payable, severity
  (critical/warning/info/ok), issue, failure_category, origin, destination, airline, created_local.
- messages: chat messages (role USER/ASSISTANT/SYSTEM, content is PII-masked, card_type). raw_intents: what the AI
  extracted (intent_type, parameters JSON). raw_flight_searches / raw_hotel_searches: every search (origin and
  destination are IATA codes; is_round_trip). raw_llm_calls: every LLM call (task, model, tokens, cost_usd, success).
  raw_analytics_events: app events (event_name, properties JSON). raw_feedback: thumbs up/down.
- users: user_id, is_guest (guest account), is_internal, signup_source, created_at.
- JSON columns are type json: use ->> to read text, e.g. parameters->>'tripType'.
- "Recurring" / "returning" users = users with chats on 2 or more distinct IST days."""

# A statement that could change data or settings, or reach outside the analytics copy, is refused before it runs.
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|grant|revoke|truncate|copy|call|do|set|reset|vacuum|analyze|"
    r"cluster|reindex|lock|listen|notify|unlisten|prepare|execute|deallocate|discard|refresh|comment\s+on|security|"
    r"pg_sleep\w*|pg_read\w*|pg_ls\w*|pg_stat_file|pg_terminate\w*|pg_cancel\w*|lo_\w+|dblink\w*|set_config|"
    r"current_setting|into)\b", re.I)
OTHER_SCHEMA = re.compile(r"\b(friction|public|pg_catalog|information_schema|pg_toast|analyst_build)\s*\.", re.I)


class AskError(Exception):
    pass


def _strip(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql or "")
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return sql.strip().rstrip(";").strip()


def check_sql(sql: str) -> str:
    """The query to run, or AskError explaining why it was refused."""
    s = _strip(sql)
    if not s:
        raise AskError("No SQL was written.")
    if ";" in s:
        raise AskError("Only one statement is allowed.")
    if not re.match(r"(?is)^\s*(select|with)\b", s):
        raise AskError("Only SELECT queries are allowed.")
    # string literals can legitimately contain words like 'update'; check the SQL with them blanked out
    bare = re.sub(r"'(?:[^']|'')*'", "''", s)
    m = FORBIDDEN.search(bare)
    if m:
        raise AskError(f"Refused: the query uses '{m.group(0)}', which is not allowed in Ask.")
    m = OTHER_SCHEMA.search(bare)
    if m:
        raise AskError(f"Refused: only the analytics tables can be queried (not {m.group(1)}.*).")
    return s


_schema_cache: dict = {}
_schema_lock = threading.Lock()


def schema_text() -> str:
    """Tables and columns of the analytics copy, rebuilt when a new sync lands."""
    with session(ANALYTICS_SCHEMA, "sync_meta") as pg:
        synced = pg.execute("SELECT synced_at FROM sync_meta").fetchone()[0]
        with _schema_lock:
            if _schema_cache.get("synced") == synced:
                return _schema_cache["text"]
        cols = pg.execute("""SELECT table_name, column_name, data_type FROM information_schema.columns
                             WHERE table_schema = %s ORDER BY table_name, ordinal_position""",
                          (ANALYTICS_SCHEMA,)).fetchall()
    tables: dict[str, list[str]] = {}
    for t, c, ty in cols:
        tables.setdefault(t, []).append(f"{c} {ty.replace('timestamp without time zone', 'timestamp').replace('double precision', 'float')}")
    text = "\n".join(f"- {t}({', '.join(cs)})" for t, cs in tables.items())
    with _schema_lock:
        _schema_cache.update(synced=synced, text=text, synced_at=str(synced))
    return text


def _sql_prompt(question: str, previous: dict | None, error: dict | None) -> str:
    prev = ""
    if previous and previous.get("question") and previous.get("sql"):
        prev = (f"\nThis is a follow-up. The previous question was: {previous['question']!r}\n"
                f"and its SQL was:\n{previous['sql']}\nReuse it where it helps.\n")
    fix = ""
    if error:
        fix = (f"\nYour previous SQL failed:\n{error['sql']}\nPostgres said: {error['message']}\n"
               "Write a corrected query.\n")
    return f"""You write ONE PostgreSQL query that answers a question about Bookkr's analytics data.

Tables in the current schema (query them without a schema prefix):
{schema_text()}

{NOTES}

Data was last synced at {_schema_cache.get('synced_at')} UTC; treat that as "now".

Rules:
- A single SELECT (CTEs allowed). Read-only. No other schemas, no functions that touch the server.
- Return at most {MAX_ROWS} rows; aggregate when the question asks for counts, trends or rankings.
- Give columns short readable snake_case names. Round percentages and money to 1 decimal place.
- For a trend over time, return the time column first (date or week), sorted ascending.
- If the question can't be answered from these tables, or is too vague to answer at all, don't write SQL: ask
  one short clarifying question instead.
{prev}{fix}
Question: {question}

Reply with ONLY a JSON object:
{{"sql": "the query, or empty when clarifying", "title": "short title for the result table",
  "chart": {{"type": "bar|line|none", "x": "column for the x axis", "y": ["numeric column(s)"]}},
  "assumptions": "one sentence on any assumption you made (date range, exclusions), or empty",
  "clarify": "a clarifying question, or empty"}}"""


def _explain_prompt(question: str, sql: str, result: dict) -> str:
    sample = result["rows"][:60]
    return f"""You are Bookkr's data analyst. Answer the question from the query result below, for a founder.

Question: {question}
SQL that ran:
{sql}
Result: {result['row_count']} rows{' (capped)' if result['truncated'] else ''}; columns {result['columns']}
First rows (JSON): {json.dumps(sample, ensure_ascii=False, default=str)}

Rules: use ONLY numbers that appear in the result. If the result is empty, say so and suggest why. Plain, direct
English, short sentences. Mention the time range when the SQL limits one.

Reply with ONLY a JSON object:
{{"answer": "2-4 sentences answering the question with the key numbers",
  "highlights": ["up to 4 short bullet points worth noticing, may be empty"],
  "caveats": "one sentence on limits of this answer, or empty"}}"""


def run_sql(sql: str) -> dict:
    with session(ANALYTICS_SCHEMA, "sync_meta") as pg:
        pg.execute("SET LOCAL transaction_read_only = on")
        pg.execute(f"SET LOCAL statement_timeout = '{TIMEOUT}'")
        # run as a subquery so a missing LIMIT can't return a whole table. Executed without parameters, so a
        # '?' or '%' in the SQL is left alone (Con would treat them as placeholders).
        cur = pg.execute(f"SELECT * FROM ({sql}\n) AS ask_q LIMIT {MAX_ROWS + 1}")
        cols = [d.name for d in cur.description]
        data = [{c: _clean(v, set(), c) for c, v in zip(cols, r)} for r in cur.fetchall()]
    return {"columns": cols, "rows": data[:MAX_ROWS], "row_count": min(len(data), MAX_ROWS),
            "truncated": len(data) > MAX_ROWS}


def ask(question: str, previous: dict | None = None) -> dict:
    question = (question or "").strip()
    if not question:
        raise AskError("Type a question first.")
    if len(question) > 1000:
        raise AskError("Keep the question under 1,000 characters.")
    if not J.llm_enabled():
        raise AskError("Ask needs Claude. Set ANTHROPIC_API_KEY (or log in to Claude Code on this machine).")
    plan = J.ask_json(_sql_prompt(question, previous, None))
    if (plan.get("clarify") or "").strip() and not (plan.get("sql") or "").strip():
        out = {"question": question, "clarify": plan["clarify"].strip()}
        _log(out)
        return out
    sql, result, attempts = plan.get("sql") or "", None, 0
    while True:
        attempts += 1
        try:
            sql = check_sql(sql)
            result = run_sql(sql)
            break
        except AskError:
            raise
        except Exception as e:  # Postgres rejected the query: one retry with the error
            if attempts >= 2:
                raise AskError(f"The query failed twice. Last error: {str(e).splitlines()[0][:300]}")
            plan = J.ask_json(_sql_prompt(question, previous, {"sql": sql, "message": str(e).splitlines()[0][:500]}))
            sql = plan.get("sql") or ""
    expl = J.ask_json(_explain_prompt(question, sql, result))
    chart = plan.get("chart") if isinstance(plan.get("chart"), dict) else {"type": "none"}
    out = {"question": question, "sql": sql, "title": plan.get("title") or "", "assumptions": plan.get("assumptions") or "",
           "chart": chart, **result, "answer": expl.get("answer") or "", "highlights": expl.get("highlights") or [],
           "caveats": expl.get("caveats") or "", "synced_at": _schema_cache.get("synced_at")}
    out["id"] = _log(out)
    return out


# Log ------------------------------------------------------------------------------------------------------------

LOG_DDL = """CREATE TABLE IF NOT EXISTS ask_log (
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  created_at TEXT NOT NULL,
  question TEXT NOT NULL,
  sql TEXT,
  answer TEXT,
  row_count INTEGER,
  result TEXT                        -- JSON: the full answer, with up to LOG_ROWS rows
)"""
_log_ready = False


def _ensure_log(c) -> None:
    global _log_ready
    if not _log_ready:
        c.execute(LOG_DDL)
        _log_ready = True


def _log(out: dict) -> int | None:
    keep = {**out, "rows": (out.get("rows") or [])[:LOG_ROWS]}
    try:
        with store_conn() as c:
            _ensure_log(c)
            r = c.execute("""INSERT INTO ask_log (created_at, question, sql, answer, row_count, result)
                             VALUES (?,?,?,?,?,?) RETURNING id""",
                          (now_iso(), out["question"], out.get("sql"), out.get("answer") or out.get("clarify"),
                           out.get("row_count"), json.dumps(keep, ensure_ascii=False, default=str))).fetchone()
        return r[0]
    except Exception:  # the answer matters more than the log
        return None


def history(limit: int = 30) -> list[dict]:
    with store_conn() as c:
        _ensure_log(c)
        return [dict(r) for r in c.execute("""SELECT id, created_at, question, row_count, answer FROM ask_log
                                              ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()]


def past(ask_id: int) -> dict | None:
    with store_conn() as c:
        _ensure_log(c)
        r = c.execute("SELECT result FROM ask_log WHERE id = ?", (ask_id,)).fetchone()
    if not r or not r[0]:
        return None
    out = json.loads(r[0])
    out["id"] = ask_id
    return out
