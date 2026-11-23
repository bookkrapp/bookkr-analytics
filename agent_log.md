# Agent log

Append-only. Newest at the bottom. See `CLAUDE.md`.

---

## 2026-11-23 22:37 IST · `main`

**Summary:** Render deployment (Dockerfile, render.yaml, optional login); moved all storage from DuckDB/SQLite files to Postgres (ANALYST_DATABASE_URL); added CLAUDE.md

**Notes:** Tested against a local Postgres only; no Neon database for ANALYST_DATABASE_URL exists yet. Not committed.

**Files:** `.claude/hooks/agent-log.sh`, `.env.example`, `.gitignore`, `AGENTS.md`, `CLAUDE.md`, `Dockerfile`, `README.md`, `backend/bookr_analyst/api.py`, `backend/bookr_analyst/config.py`, `backend/bookr_analyst/db.py`, `backend/bookr_analyst/friction/__main__.py`, `backend/bookr_analyst/friction/export.py` (+12 more)
