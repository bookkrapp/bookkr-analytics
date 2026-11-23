# Agent log

Append-only. Newest at the bottom. See `CLAUDE.md`.

---

## 2026-11-23 22:37 IST · `main`

**Summary:** Render deployment (Dockerfile, render.yaml, optional login); moved all storage from DuckDB/SQLite files to Postgres (ANALYST_DATABASE_URL); added CLAUDE.md

**Notes:** Tested against a local Postgres only; no Neon database for ANALYST_DATABASE_URL exists yet. Not committed.

**Files:** `.claude/hooks/agent-log.sh`, `.env.example`, `.gitignore`, `AGENTS.md`, `CLAUDE.md`, `Dockerfile`, `README.md`, `backend/bookr_analyst/api.py`, `backend/bookr_analyst/config.py`, `backend/bookr_analyst/db.py`, `backend/bookr_analyst/friction/__main__.py`, `backend/bookr_analyst/friction/export.py` (+12 more)

---

## 2026-11-23 23:11 IST · `main`

**Summary:** Manual run of the daily pipeline against the Neon analyst DB, no API key: sync, Friction Finder for 2026-11-22 (114 chats judged in Claude Code), 33 issues, day/last7/week briefs, RAG pack

**Notes:** No code changed. Judge verdicts came from Claude Code subagents and were loaded through runner.run_day; grouping and briefs written in-session through issues.py. Only 22 Nov is judged; 2-21 Nov and 23 Nov are not.

**Files:** _none in the working tree — already committed?_

---

## 2026-11-23 23:13 IST · `main`

**Summary:** Added backend/scripts/run_day.py: one-date run of sync, Friction Finder (judge via Claude Code CLI, no API key), issue grouping, briefs and RAG pack; refuses future dates

**Notes:** Tested date checks and one CLI model call; a full day was not run through the script. README Friction Finder section updated.

**Files:** `README.md`, `agent_log.md`, `backend/scripts/run_day.py`
