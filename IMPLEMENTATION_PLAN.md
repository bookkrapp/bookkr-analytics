# Bookr Analyst — Detailed Implementation Plan

Companion to [ARCHITECTURE.md](ARCHITECTURE.md). Phased so that something useful works end-to-end early, then gets hardened.

---

## Project layout

```
Bookr Analyst/
├── ARCHITECTURE.md
├── IMPLEMENTATION_PLAN.md
├── pyproject.toml
├── .env.example                 # ANTHROPIC_API_KEY, DATABASE_URL_RO, CHECKPOINT_DB_URL, LANGSMITH_API_KEY
├── langgraph.json               # for LangGraph Studio / Server
├── context/                     # MD knowledge base (see ARCHITECTURE §4)
│   ├── schema/_overview.md
│   ├── schema/<table>.md
│   ├── metrics.md
│   ├── business_rules.md
│   ├── glossary.md
│   └── examples.md
├── src/bookr_analyst/
│   ├── config.py                # settings via pydantic-settings
│   ├── state.py                 # AnalystState
│   ├── graph.py                 # StateGraph wiring + conditional edges
│   ├── llm.py                   # ChatAnthropic factory (model, effort, caching)
│   ├── prompts/                 # one prompt file per LLM node
│   ├── nodes/
│   │   ├── understand.py
│   │   ├── retrieve_context.py
│   │   ├── generate_sql.py
│   │   ├── validate_sql.py
│   │   ├── execute_sql.py
│   │   ├── analyze.py
│   │   └── compose.py
│   ├── context_loader.py        # parse MD + front-matter → docs + allowlist
│   ├── db.py                    # Neon pool, read-only session setup
│   └── sql_guard.py             # sqlglot-based validator
├── api/main.py                  # FastAPI (Phase 4)
├── ui/streamlit_app.py          # demo UI (Phase 3)
├── scripts/
│   ├── schema_drift_check.py
│   └── bootstrap_context.py     # generate draft MD from information_schema
├── evals/
│   ├── golden_questions.yaml
│   └── run_evals.py
└── tests/
    ├── test_sql_guard.py
    ├── test_context_loader.py
    └── test_graph_routes.py
```

**Dependencies:** `langgraph`, `langchain-core`, `langchain-anthropic`, `langgraph-checkpoint-postgres`, `psycopg[binary,pool]`, `sqlglot`, `pandas`, `pydantic-settings`, `python-frontmatter`, `langsmith`, `fastapi`, `uvicorn`, `streamlit`, `pytest`.

---

## Phase 0 — Foundations (1–2 days)

**Goal:** environment, safe DB access, model access verified.

1. Create the Python project (`uv` or `poetry`), `.env.example`, `config.py`.
2. **Neon setup** (do this in the Neon console / SQL, not via the agent):
   - Create a read replica (or a dedicated branch) for analytics.
   - Create role `analyst_ro`; `GRANT USAGE` on schema `analytics`; `GRANT SELECT` on approved views only.
   - Create `analytics.*` views that exclude PII and test data.
   - `ALTER ROLE analyst_ro SET default_transaction_read_only = on;` and `SET statement_timeout = '15s';`
   - Separate database/schema + role for the LangGraph checkpointer and audit log.
3. `db.py`: `ConnectionPool` on the pooled endpoint, `sslmode=require`, helper `run_select(sql) -> (rows, meta)` with row cap.
4. **Spike:** call `claude-opus-5-5` through `ChatAnthropic` and confirm: structured output works without forced tool choice, effort setting is passed, prompt caching shows `cache_read_input_tokens > 0` on the second call.

**Done when:** a script can run `SELECT 1` as `analyst_ro`, an `INSERT` attempt fails, and the model spike returns a parsed structured object.

---

## Phase 1 — Context knowledge base (2–4 days, mostly business input)

**Goal:** MD files that describe the data accurately. This is the biggest accuracy lever.

1. `scripts/bootstrap_context.py`: read `information_schema` + `pg_description` comments and write draft `context/schema/<table>.md` files with front-matter (table, columns, types).
2. Humans (someone who knows Bookkr's data) enrich each file: meaning of columns, enum values, join paths, gotchas.
3. Write `metrics.md` — each metric with a definition **and** canonical SQL snippet (revenue, bookings, ADR, occupancy, cancellation rate, repeat-guest rate, lead time…).
4. Write `business_rules.md` (timezone, date semantics like check-in vs created date, currency conversion, exclusions) and `glossary.md` (synonyms).
5. Write `examples.md` — start with 20–30 real questions the team asks, each with verified SQL.
6. `context_loader.py`: load all MD, parse front-matter into a `{table: set(columns)}` allowlist, and build the static system-prompt context block.
7. `scripts/schema_drift_check.py`: diff front-matter vs `information_schema`.

**Done when:** every exposed view has a doc file, drift check passes, and examples run successfully against Neon.

---

## Phase 2 — Core graph, CLI (3–5 days)

**Goal:** question in → correct answer out, in a terminal.

1. `state.py` — `AnalystState` (ARCHITECTURE §2).
2. `sql_guard.py` (write tests first):
   - parse with `sqlglot.parse(sql, read="postgres")`; exactly one statement;
   - root must be `Select`/`Union`/`With`→`Select`;
   - walk all `Table`/`Column` nodes, check against allowlist (resolve aliases/CTEs);
   - block functions: `pg_sleep`, `dblink*`, `lo_*`, `pg_read_file`, `set_config`, etc.;
   - add/clamp `LIMIT` (default 1000, max 5000).
3. Nodes, in this order:
   - `generate_sql` + `validate_sql` + `execute_sql` with the retry loop (cap 3) — test with direct questions first.
   - `analyze_results`: pandas summary (row_count, column stats, group totals, period-over-period deltas when there's a date column); LLM gets stats + ≤50-row sample and returns `{insights, needs_followup, followup_question}`.
   - `compose_answer`.
   - `understand_question` (intent + standalone rewrite + clarification).
   - `retrieve_context` (v1: return the full static context).
4. `graph.py`: wire nodes + conditional edges; compile with `InMemorySaver` for now.
5. `llm.py`: per-node effort (`high` for SQL/analysis, `low` for routing/compose), system prompt with cached context block first, refusal check + `fallbacks: "default"`.
6. Prompts — key rules for `generate_sql`:
   - only use tables/columns from the provided context;
   - use metric definitions from `metrics.md` verbatim;
   - always state assumptions (date range, status filters);
   - prefer explicit column lists, aggregate in SQL, never return raw PII.
7. CLI: `python -m bookr_analyst "How many bookings did we get last month by city?"`.

**Done when:** 10 hand-picked questions answered correctly from the CLI; failing SQL self-repairs; destructive SQL is blocked by the guard.

---

## Phase 3 — Evaluation & accuracy loop (3–4 days, then ongoing)

1. `evals/golden_questions.yaml`: 50–100 entries `{question, expected_sql, expected_result_hash or key values, tags}`. Cover: simple counts, time ranges, joins, metrics, ambiguous questions (expect clarification), out-of-scope, and malicious ("delete all bookings").
2. `run_evals.py` with LangSmith datasets: execution accuracy, valid-SQL rate, avg retries, faithfulness (numbers in answer ∈ analysis), latency, tokens/cost.
3. Iterate: most fixes go into the MD context (missing rule, unclear metric) rather than prompts.
4. Streamlit demo UI (`ui/streamlit_app.py`) showing answer, SQL used, result table, and a thumbs up/down.

**Target before wider rollout:** ≥ 85–90% execution accuracy on the golden set, 100% block rate on malicious cases.

---

## Phase 4 — Production hardening (4–6 days)

1. Swap `InMemorySaver` → `PostgresSaver` (checkpoint DB) for multi-turn memory.
2. FastAPI `api/main.py`: `POST /chat` (SSE streaming of node progress + final answer), `POST /feedback`; auth (API key/JWT); per-user rate limit.
3. If per-hotel access is needed: RLS policies on views + set `app.hotel_id` on the connection per request from the authenticated user.
4. Audit table `analyst_audit(user_id, thread_id, question, sql, row_count, duration_ms, created_at)`.
5. `EXPLAIN` pre-check: reject queries whose estimated cost/rows exceed a threshold.
6. Error UX: graceful messages when the retry budget is exhausted ("I couldn't build a reliable query for X — can you clarify Y?").
7. Deploy (Docker → Render/Fly/AWS, or LangGraph Platform); secrets in the platform's secret store.

---

## Phase 5 — Enhancements (as needed)

- **Retrieval** over context when docs exceed ~80K tokens (pgvector in Neon).
- **Learning loop:** thumbs-up answers become candidate examples after human review.
- **Charts:** return a Vega-Lite/Plotly spec from `compose_answer` for the UI to render.
- **Scheduled insights:** weekly digest ("bookings down 12% WoW in Goa").
- **Cost tuning:** evaluate `claude-sonnet-5-5` on routing/compose nodes against the eval set.

---

## Prompt sketches

**generate_sql (system, cached part):**
```
You are a senior analytics engineer for Bookkr. Write one PostgreSQL SELECT query
that answers the question, using ONLY the tables and columns documented below.
Use the metric definitions in METRICS exactly. If a date range is not given,
default per BUSINESS RULES and state it as an assumption.
<schema>…</schema> <metrics>…</metrics> <business_rules>…</business_rules>
<glossary>…</glossary> <examples>…</examples>
```
**generate_sql (per call):** question, chat summary, and on retry: previous SQL + exact error.
Output schema: `{sql: str, tables_used: list[str], assumptions: list[str]}`.

**compose_answer:**
```
Answer the user's question using ONLY the numbers in ANALYSIS. Lead with the direct
answer, then 2–4 supporting insights, then assumptions. Keep it short and plain.
If the result is empty, say so and suggest why.
```

---

## Timeline summary

| Phase | Effort | Outcome |
|---|---|---|
| 0 Foundations | 1–2 d | Safe Neon access, model verified |
| 1 Context KB | 2–4 d | Accurate MD docs + allowlist |
| 2 Core graph | 3–5 d | Working CLI agent |
| 3 Evals | 3–4 d | Measured accuracy, demo UI |
| 4 Hardening | 4–6 d | API, memory, RLS, audit, deploy |
| **Total MVP → prod** | **~2.5–4 weeks** (one developer) | |

## Open questions to settle before Phase 1
1. Which tables/schemas should the agent be allowed to see? Any PII that must be masked?
2. Is it internal-only, or will hotel partners use it (→ RLS per hotel)?
3. Where will users interact — internal dashboard, Slack, or inside the Bookkr app?
4. Who owns the MD context files and reviews changes to metric definitions?
