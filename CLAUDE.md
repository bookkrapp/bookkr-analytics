# CLAUDE.md

Guidance for coding agents in this repository. `AGENTS.md` is a symlink to this file.

This file holds only what must be true on every turn. Everything else lives in
the docs listed under "Where to look" and is read when the task needs it. The
rules mirror the main Bookkr repository's `CLAUDE.md`, adapted to this codebase.

---

## Hard rules

**1 — Never run a command that can write to, reset, or drop a database without
explicit confirmation in this conversation.** Every URL in `.env` is real data.

- `SOURCE_DATABASE_URL` is the live Bookkr database. It is only ever read, inside
  the read-only transaction in `backend/bookr_analyst/sync.py`. Never run DDL or
  DML against it, with any tool.
- `ANALYST_DATABASE_URL` must never be Bookkr's database (`bookkrdb`). `db.py`
  refuses to connect if it matches `SOURCE_DATABASE_URL` or a name in
  `PROTECTED_DATABASES`.
- `ANALYST_DATABASE_URL` is one Neon database shared by every laptop and the
  deployed app. Its `friction` schema holds human reviews that cannot be
  regenerated. Ask before running `python -m bookr_analyst.friction --reset`,
  `scripts/migrate_sqlite_to_postgres.py --replace`, or any `DROP`, `TRUNCATE`,
  `DELETE` or `ALTER SCHEMA`.
- A sync (`python -m bookr_analyst.sync`, or **Refresh data**) replaces the
  `analyst` schema for everyone using the database. It is safe, but it is not local.

**2 — Read the docs for the area you are about to edit.** Use the table below.
There is no hook enforcing it here; the rule still holds.

**3 — Log what you changed.** As the last action of any session that modified
files:

```bash
.claude/hooks/agent-log.sh "one-line summary of what you did"
```

**4 — Only masked data leaves the source.** A new column or table in
`sync.py → EXTRACTS` must go through `masking.py` and be listed in
`DATA_DICTIONARY.md`. Emails, phones, names, traveller details, PNRs and booking
request/response JSON are never copied.

**5 — Secrets stay server-side.** `VITE_*` values are inlined into the shipped
bundle and extractable from a build. Connection strings and `ANTHROPIC_API_KEY`
live in `.env` (git-ignored) or the Render dashboard, never in code or docs.

---

## Where to look

| Working on | Read |
|---|---|
| Anything — start here | [TRACKER.md](TRACKER.md) (team rules, open inputs), [README.md](README.md) |
| How the pieces fit | [ARCHITECTURE.md](ARCHITECTURE.md), [PLATFORM_PLAN.md](PLATFORM_PLAN.md) |
| `backend/bookr_analyst/sync.py`, `masking.py`, `transform.py` | [DATA_DICTIONARY.md](DATA_DICTIONARY.md) |
| Database access, schemas, the sync swap | `backend/bookr_analyst/db.py` and the "How it works" section of the README |
| Dashboards and metric definitions | [REPORTING_USE_CASES.md](REPORTING_USE_CASES.md), README "Metric definitions" |
| `backend/bookr_analyst/friction/`, `config/friction.json` | README "Friction Finder" |
| `.env`, deployment | [.env.example](.env.example), [render.yaml](render.yaml), [Dockerfile](Dockerfile) |

One FastAPI backend (`backend/`) serves the React UI (`frontend/`). The UI reads
the API's JSON shapes directly, so changing a response shape is a two-sided
change. Analytics SQL is Postgres; call sites write `$name` or `?` placeholders,
which `db.py` translates.

---

## Before you answer an architecture question

If `graphify-out/` exists, use the knowledge graph instead of reading source file
by file (`graphify-out/GRAPH_REPORT.md`), and refresh it after changing code.
This repository has no committed graph yet; until it does, start from
`ARCHITECTURE.md` and `SYSTEM_MAP.html`.

---

## After you write code

Run `/simplify`, then verify. There is no test suite yet, so verifying means:

```bash
cd frontend && npm run build        # type-checks and builds the UI
docker build -t bookr-analyst .     # the image Render deploys
```

For backend changes, run the app against a database and exercise the endpoints
you touched; say so plainly if you could not.
