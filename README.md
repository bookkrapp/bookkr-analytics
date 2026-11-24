# Bookr Analyst

Internal analytics platform for Bookkr.
- **Dashboards:** Overview, Funnel, Booking & payment ops, Users & retention, Conversations (explorer). Every KPI has an ⓘ button that explains it.
- **Friction Finder:** finds chats where users had to repeat or correct themselves. The team reviews each one, and agreed items are exported as a RAG / few-shot dataset for Bookkr's intent extraction.

**Start every working session with [TRACKER.md](TRACKER.md)**: team rules (e.g. mandatory fields) and the inputs we're waiting on.

Plan docs: [PLATFORM_PLAN.md](PLATFORM_PLAN.md) · [DATA_DICTIONARY.md](DATA_DICTIONARY.md) · [REPORTING_USE_CASES.md](REPORTING_USE_CASES.md) · [ARCHITECTURE.md](ARCHITECTURE.md)

## Run it

```powershell
.\start.ps1
```
Then open **http://localhost:8000**. The first run creates the Python env and builds the UI; if the database has never been synced, the server starts a sync.
Use the **Refresh data** button (top right) to re-sync from the source database.

`.env` needs two connection strings (see `.env.example`):
- `SOURCE_DATABASE_URL`: the Bookkr database, read with a read-only login.
- `ANALYST_DATABASE_URL`: Bookr Analyst's **own** Neon database. Everything the app stores lives here, so a laptop and the deployed app that use the same value see the same data, reviews and exports. It must not be the source database.

### Deploy (Render)
`render.yaml` deploys one Docker web service (`Dockerfile` builds the UI and serves it from the API). Set the same `ANALYST_DATABASE_URL` there. The container keeps no state, so no disk is needed. Set `BASIC_AUTH_PASSWORD` to put the site behind a login.
Set `FRICTION_DAILY_AT` in **one** place only (normally Render): two servers with the schedule on would both run the daily analysis.

Development mode (hot reload):
```powershell
# terminal 1 — API
cd backend; .\.venv\Scripts\python.exe -m uvicorn bookr_analyst.api:app --reload --port 8000
# terminal 2 — UI at http://localhost:5173 (proxies /api to :8000)
cd frontend; npm run dev
```
Sync from the command line: `cd backend; .\.venv\Scripts\python.exe -m bookr_analyst.sync`

## How it works

```
Neon (source)  ──read-only, one REPEATABLE READ snapshot──►  sync.py
                                                                 │  drop PII columns, regex-mask text & JSON
                                                                 ▼
                                  Postgres, schema `analyst`  (raw_* tables)
                                                                 │  transform.py
                                                                 ▼
                              users · messages · session_facts · booking_ops · stages
                                                                 │
                                    FastAPI (backend/bookr_analyst/api.py)  ──►  React UI (frontend/)
```

| Piece | File |
|---|---|
| Settings (.env) | `backend/bookr_analyst/config.py` |
| Postgres access (pool, schemas) | `backend/bookr_analyst/db.py` |
| PII masking | `backend/bookr_analyst/masking.py` |
| Extract + swap-in | `backend/bookr_analyst/sync.py` |
| Derived tables & metric definitions | `backend/bookr_analyst/transform.py` |
| API endpoints | `backend/bookr_analyst/api.py` (`/api/overview`, `/api/funnel`, `/api/ops`, `/api/sessions`, `/api/sessions/{id}`, `/api/meta`, `POST /api/sync`) |
| UI pages | `frontend/src/pages/*.tsx` |

### Safety & privacy
- The sync opens the source connection with `read_only = True` and a `statement_timeout`, so it cannot write. **Still, `.env` currently uses the `neondb_owner` login.** Create the read-only role in [sql/create_readonly_role.sql](sql/create_readonly_role.sql) and swap the connection string. Also reset the owner password, because it was shared in chat.
- These are **never copied**: emails, phones, names, DOB, password hashes, guest secrets, GST info, traveller details, booking request/response JSON, PNRs (only a "has PNR" flag), and user location.
- Message text, titles, intent JSON and failure reasons are regex-masked (emails → `[email]`, phones → `[phone]`, long numbers, passport-like IDs). **Personal names inside free text are not masked yet.** That needs NER/LLM masking, planned with the Friction Finder.
- `.env` and `data/` (local export files) are git-ignored.
- The sync writes a fresh copy into a build schema in one transaction and swaps it in for `analyst` on commit. The `friction` schema (findings, reviews, exports) is never touched by a sync.

### Metric definitions (in `transform.py`)
- **Funnel stage** = furthest stage a chat session reached: Chat created → User engaged (≥1 user message) → Intent captured (intent-confirmation card, flow state `intent_ready`, or a multi-city phase) → Search run (flight or hotel search row) → Results shown (flight/hotel results card or panel) → Booking attempted → Booked (`FlightBooking.status = BOOKED`).
- **Journey** (`session_facts.journey_type`, the Funnel and Conversations filter) = what the user came to do, first match wins: *Trip planning* (a plan card, the `multi_city` flow, a `multi_city_trip_planning` intent or a `trip_plan_options` panel) → *Flight + hotel* (searched, or for chats with no search asked for, both) → *Round-trip flight* (a round-trip search, or a `ROUND_TRIP` intent when no search ran) → *One-way flight* → *Hotel only* → *Not clear yet*. Needs a sync after deploy (Refresh data) to appear.
- **Booked / GMV** = `FlightBooking.status = 'BOOKED'`, sum of `amountPayable` (INR). Hotel bookings are not included yet.
- **Ops severity**: *critical* = money taken but no confirmed booking (`FAILED` with `CAPTURED`/`VOID_PENDING`/`CAPTURED_AWAITING_PROVIDER`, or pending with a captured payment). *Warning* = pending for more than 30 minutes, or booked without a confirmation email. *Info* = failed but resolved, or failed before payment.
- **Times**: the source stores UTC (Prisma), and everything is shown in IST (`TZ_OFFSET_MINUTES=330`).

### Internal / test users
Put internal user IDs or email domains in `.env` (`INTERNAL_USER_IDS`, `INTERNAL_EMAIL_DOMAINS`), then refresh. The **Exclude internal users** toggle hides them everywhere. Nothing is configured yet, so the toggle currently changes nothing. Of 261 users, 257 are guest accounts (`anonymous.bookkr.local`).

## Friction Finder

```
one day of chats ──► rule signals (score) ──► LLM judge (Claude, optional) ──► findings ──► human review ──► RAG file
 (masked, schema analyst) friction/signals.py    friction/judge.py            schema friction        /friction/:id   data/exports/ + DB
```

- **Run it:** in the app, go to Friction Finder → pick a day → **Run analysis**. Or from the command line:
  `cd backend; .\.venv\Scripts\python.exe -m bookr_analyst.friction --sync --day 2026-11-20` (default day = yesterday, IST; `--rules-only` skips the LLM).
- **One day, end to end, without an API key:** `cd backend; python scripts/run_day.py 2026-11-22` syncs, judges that day through the Claude Code CLI, groups the items into issues, writes the briefs and refreshes the RAG pack (the same steps as the daily schedule). A future date is refused; `--skip-sync` reuses the data already synced.
- **Daily, automatically:** set `FRICTION_DAILY_AT=09:00` in `.env`. While the server runs, it syncs and analyses yesterday's chats at that time each day.
- **Which chats:** a chat belongs to the IST day of its *last* user message, so it is analysed once, with its full history.
- **Rule R1:** the assistant asking for a mandatory field is never friction. The user repeating a value they had already given *is*. The fields are in `config/friction.json → ask_patterns` (still to be confirmed by the team, see TRACKER Q1).
- **LLM judge on a Claude subscription (no API key):** install Claude Code, run `claude` once and log in with your Pro/Max account, then restart the server. The judge calls `claude -p` in an empty temp folder with tools disabled, so usage counts against your plan's limits rather than a per-token bill. With `FRICTION_LLM_PROVIDER=auto` this is used whenever there's no API key; set `CLAUDE_CLI_PATH` if `claude` isn't on PATH.
- **LLM judge on an API key:** set `ANTHROPIC_API_KEY` in `.env` and restart. Chats with a rule score ≥ 1 are sent to `claude-sonnet-5-5` (switch to `claude-opus-5-5` in `config/friction.json` if precision is low) (structured JSON output, server-side refusal fallback enabled), which writes for each item: the turns, the quote, what went wrong, the correct interpretation (travel_intent JSON) and a guidance rule. Items reviewers mark **Not friction** are fed back into the prompt as known false positives. Without a key, it runs rules-only.
- **Review:** each item gets **Agree** (goes into the dataset), **Not friction** (teaches the judge), or **Skip**. Reviewers can edit the interpretation and guidance. Re-running a day never touches chats that have already been reviewed.
- **Export:** Dataset & export → **Generate file** writes `data/exports/bookkr_friction_rag_<time>.jsonl` (also stored in the database, so it can be downloaded from any machine) (one example per line: context, user_message, what_went_wrong, expected_interpretation, guidance, source) and a `.md` with the rules plus readable examples.
- **Tuning:** weights, thresholds, regexes and the model are in `config/friction.json`, picked up on the next run.

## Ask

The **Ask** page answers plain-English questions about the data (`backend/bookr_analyst/ask.py`):

1. Claude writes one PostgreSQL query from the live table list plus the notes in `ask.py → NOTES` (keep those up to date when tables change).
2. The query is checked: a single `SELECT`/`WITH`, no writes, no settings or server functions, no schema other than `analyst`. It then runs in a **read-only transaction** with a 20 s timeout and a 500-row cap. If Postgres rejects it, Claude gets the error once to fix it.
3. Claude explains the rows it got back, using only numbers from the result. The page shows the answer, a chart when it fits, the table and the SQL. Follow-up questions reuse the previous SQL.

Every question is logged in `friction.ask_log` (question, SQL, answer, first 200 rows). It uses the same Claude account as the Friction Finder judge; on Render that means `ANTHROPIC_API_KEY`.

## Moving an old local store to Postgres
Earlier versions kept data in local files (`data/analytics.duckdb`, `data/friction.sqlite`). The analytics copy is rebuilt by a sync. To carry over findings, reviews and export files:
`cd backend; python scripts/migrate_sqlite_to_postgres.py` (reads `data/friction.sqlite`, never changes it; refuses to overwrite a non-empty store unless `--replace` is given).

## Moving to production data
1. Create `bookr_analyst_ro` on production (ideally on a read replica) with `sql/create_readonly_role.sql`.
2. Set `SOURCE_DATABASE_URL` to it, configure the internal users (`INTERNAL_USER_IDS`), and refresh.
3. **Clear the test-data review queue:** `cd backend; .\.venv\Scripts\python.exe -m bookr_analyst.friction --reset`. This renames the `friction` schema to `friction_archived_<time>` so test findings don't end up in the production dataset. It affects every machine using the database.
4. Set `ANTHROPIC_API_KEY` for the LLM judge (optional but recommended), then run the Friction Finder on the first production day.
5. At production volume, switch the sync from full refresh to incremental (by `updatedAt`). It is a full copy today.
