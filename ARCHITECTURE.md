# Bookr Analyst — Architecture

An AI data analyst that answers plain-English business questions by writing SQL, running it against the Bookkr Neon (Postgres) database, analysing the result, and replying in natural language.

**Stack:** Python 3.11+, LangGraph (orchestration), LangChain (`langchain-anthropic` model wrapper, tool abstractions), Claude Opus 5.5 (`claude-opus-5-5`), Neon Postgres (`psycopg` 3), `sqlglot` (SQL validation), `pandas` (result analysis), LangSmith (tracing/evals).

---

## 1. High-level flow

```
                ┌──────────────────────────────────────────────────────────┐
 User question  │                    LangGraph StateGraph                  │
 ─────────────► │                                                          │
                │  [1] understand_question ──(needs clarification)──► END  │
                │          │ (data question)            (asks user back)   │
                │          ▼                                               │
                │  [2] retrieve_context  ◄── MD knowledge base (context/)  │
                │          │                                               │
                │          ▼                                               │
                │  [3] generate_sql  ◄──────────────┐                      │
                │          │                        │ error + feedback     │
                │          ▼                        │ (max 3 attempts)     │
                │  [4] validate_sql ──(invalid)─────┤                      │
                │          │ (valid)                │                      │
                │          ▼                        │                      │
                │  [5] execute_sql ──(DB error)─────┘                      │
                │          │ (rows)        ▲                               │
                │          ▼               │ Neon (read-only role)         │
                │  [6] analyze_results     │                               │
                │          │  (may loop to [3] for a follow-up query)      │
                │          ▼                                               │
                │  [7] compose_answer ──────────────────────────────► END  │
                └──────────────────────────────────────────────────────────┘
                         │
                         ▼
             Checkpointer (Postgres) — conversation memory per thread_id
```

### Why an explicit graph instead of a free-form ReAct agent
- **Guardrails are nodes, not hopes.** SQL validation and the retry budget are enforced in code, so the model can never skip them.
- **Predictable cost/latency.** Bounded loops (max SQL attempts, max follow-up queries).
- **Debuggable.** Every node's input/output is visible in LangSmith; failures point at a specific step.
- The LLM still has freedom where it matters: writing SQL, deciding whether a follow-up query is needed, and interpreting results.

---

## 2. Graph state

```python
class AnalystState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]  # full chat history
    question: str                 # current user question (resolved w/ history)
    intent: Literal["data_question", "clarify", "smalltalk", "out_of_scope"]
    context_docs: list[str]       # MD sections selected for this question
    sql: str | None               # current candidate query
    sql_attempts: int             # retry counter (cap = 3)
    sql_error: str | None         # validator or DB error fed back to generator
    result_rows: list[dict] | None
    result_meta: dict | None      # columns, dtypes, row_count, truncated flag
    analysis: dict | None         # computed stats (pandas) + LLM insights
    queries_run: list[dict]       # audit trail: [{sql, row_count, ms}]
    final_answer: str | None
```

---

## 3. Nodes

| # | Node | Type | Responsibility |
|---|------|------|----------------|
| 1 | `understand_question` | LLM (structured output) | Rewrite follow-ups into a standalone question using chat history ("what about last month?" → full question). Classify intent. If ambiguous (e.g. "revenue" — gross or net?), return a clarifying question and end the turn. |
| 2 | `retrieve_context` | Code (+ optional embeddings) | Select the relevant MD context: schema docs for involved tables, metric definitions, business rules, few-shot example queries. See §4. |
| 3 | `generate_sql` | LLM (structured output) | Produce `{sql, tables_used, reasoning_summary, assumptions}`. On retry, receives the previous SQL + the exact error. |
| 4 | `validate_sql` | Code | Parse with `sqlglot` (Postgres dialect). Reject anything not a single `SELECT`/`WITH … SELECT`. Check tables/columns against an allowlist built from the schema docs. Inject `LIMIT` if missing. Reject `SELECT *` on wide tables. |
| 5 | `execute_sql` | Tool (Neon) | Run on a read-only connection with `statement_timeout`. Return rows (capped, e.g. 5,000) + metadata. DB errors go back to node 3. |
| 6 | `analyze_results` | Code + LLM | Code computes deterministic numbers with pandas (totals, % change, top-N, averages, trends) — **the LLM never does arithmetic on raw rows**. LLM then reads the stats + a row sample and decides: answer now, or run one follow-up query (cap = 2 follow-ups). |
| 7 | `compose_answer` | LLM | Natural-language answer: headline number first, short explanation, key caveats/assumptions, optionally the SQL used (collapsible) and a suggested chart type. |

### Conditional edges
- `understand_question` → `retrieve_context` | `END` (clarify / smalltalk / out_of_scope).
- `validate_sql` → `execute_sql` | `generate_sql` (if `sql_attempts < 3`) | `compose_answer` (give up gracefully, explain what failed).
- `execute_sql` → `analyze_results` | `generate_sql` (on error, same cap).
- `analyze_results` → `compose_answer` | `generate_sql` (follow-up query).

---

## 4. Context layer (the MD files)

The MD files are the agent's "knowledge of the business". Quality here drives accuracy more than anything else.

```
context/
├── schema/
│   ├── _overview.md          # ER summary, how tables join, naming conventions
│   ├── bookings.md           # one file per table: purpose, columns, types,
│   ├── hotels.md             #   meaning, enum values, nullability, gotchas,
│   ├── guests.md             #   join keys, row-count scale
│   └── ...
├── metrics.md                # canonical definitions: "revenue", "ADR",
│                             #   "occupancy", "cancellation rate" → exact SQL
├── business_rules.md         # timezones, fiscal calendar, soft-deletes,
│                             #   test data to exclude, currency handling
├── glossary.md               # synonyms: "stays" = bookings w/ status='completed'
└── examples.md               # 20–50 question → SQL pairs (few-shot)
```

Each schema file uses YAML front-matter so it can be parsed into the validator's allowlist:

```markdown
---
table: bookings
columns: [id, hotel_id, guest_id, check_in, check_out, status, total_amount, currency, created_at]
---
# bookings
One row per reservation. `status` ∈ {pending, confirmed, cancelled, completed}.
- `total_amount` is in `currency`; convert with `fx_rates` for cross-currency totals.
- Always exclude `is_test = true`.
```

### Retrieval strategy (two stages)
1. **Start simple (v1):** load *all* MD files into the system prompt. If total context is under ~50–80K tokens this is the most accurate option, and **prompt caching** makes it cheap (the context block is identical on every call, so it's cached).
2. **Scale up (v2, only if needed):** when the docs outgrow that, add retrieval — always include `_overview.md`, `metrics.md`, `business_rules.md`; pick table docs and examples by embedding similarity (pgvector in Neon works well) plus keyword match on table/metric names.

Also add a **schema-drift check** script: compares `information_schema` with the MD front-matter and flags undocumented/removed columns, so docs don't silently rot.

---

## 5. Neon connection & safety (non-negotiable)

| Layer | Control |
|---|---|
| Database role | Dedicated `analyst_ro` role with `SELECT` only on approved schemas/views. No `INSERT/UPDATE/DELETE/DDL` grants. |
| Isolation | Point the agent at a **Neon read replica** (or a dedicated branch) so heavy analytical queries never slow production. |
| Session settings | `SET default_transaction_read_only = on; SET statement_timeout = '15s'; SET idle_in_transaction_session_timeout = '30s';` |
| SQL validator | Single statement, SELECT-only, table/column allowlist, forced `LIMIT`, no `pg_*`/system catalogs, no functions like `pg_sleep`, `dblink`, `copy`. |
| Sensitive data | Expose curated **views** (e.g. `analytics.bookings_v`) that drop/mask PII (guest emails, phones, payment info). The agent's allowlist only contains these views. |
| Multi-tenant (if hotels see only their own data) | Enforce with Postgres **Row-Level Security** + `SET app.hotel_id = …` per request. Never rely on the LLM to add a `WHERE hotel_id = …`. |
| Result size | Cap rows returned to the LLM (e.g. 200 sampled + full stats from pandas). |
| Secrets | `DATABASE_URL` and `ANTHROPIC_API_KEY` from env / secret manager, never in MD files or prompts. |

Connection: `psycopg_pool.ConnectionPool` (Neon pooled endpoint `-pooler` host), `sslmode=require`.

> Alternative considered: Neon's MCP server as the tool. It's convenient for prototyping but exposes more than we want (branch/project management). A narrow custom `run_sql` tool with our own validator is safer for production.

---

## 6. Model usage

- **Model:** `claude-opus-5-5` via `langchain_anthropic.ChatAnthropic` for SQL generation, analysis and answer writing.
- **Effort:** Opus 5.5 defaults to `medium` effort; set it explicitly. Use `high` for `generate_sql`/`analyze_results`, `low` for `understand_question`/`compose_answer`.
- **Structured outputs:** Opus 5.5 rejects *forced* `tool_choice` (`any`/specific tool). When using `with_structured_output`, use the JSON-schema / native structured-output method rather than a mode that forces a tool call. Verify this in a spike (Phase 0).
- **Prompt caching:** put the static MD context first in the system prompt with a cache breakpoint; the per-question content comes after it.
- **Refusal handling:** check `stop_reason == "refusal"` and enable server-side fallbacks (`fallbacks: "default"`) so a rare false-positive doesn't break a turn.
- **Cost lever later:** if volume grows, test `claude-sonnet-5-5` on the cheap nodes (classification, answer composition) against the eval set before switching anything.

---

## 7. Memory

- **Short-term (conversation):** LangGraph `PostgresSaver` checkpointer, keyed by `thread_id`. Store it in a *separate* Neon database/schema with its own read-write role — never the analytics role.
- Enables follow-ups: "break that down by city", "now compare with 2025".
- **Long-term (optional, v2):** save verified question→SQL pairs (thumbs-up from users) into `examples.md`/a table — the agent gets better as it's used.

---

## 8. Interfaces

```
           ┌────────────┐     ┌──────────────────┐     ┌──────────────┐
 Users ──► │  Chat UI   │ ──► │ FastAPI service  │ ──► │  LangGraph   │ ──► Neon
           │ (Streamlit │     │ /chat (SSE stream)│     │  app.invoke  │
           │  / web app)│     │ /feedback         │     └──────────────┘
           └────────────┘     └──────────────────┘            │
                                                              ▼
                                                         LangSmith traces
```

- **v1:** CLI + LangGraph Studio for development; Streamlit for internal demo.
- **v2:** FastAPI with streaming (SSE) so users see "Writing query… Running… Analysing…" progress; can later be deployed via LangGraph Platform/Server.

---

## 9. Observability & evaluation

- **Tracing:** LangSmith on every run (prompt, SQL, row counts, latency, tokens, errors).
- **Audit log:** every executed SQL + user + timestamp + row count stored in an `analyst_audit` table.
- **Eval set:** 50–100 real business questions with expected SQL **and** expected result values. Metrics:
  - *Execution accuracy* — does the result set match the expected result? (primary)
  - *Valid-SQL rate*, *retries per question*, *answer faithfulness* (numbers in the text match the data), latency, cost per question.
- Run evals on every prompt/context change (CI).

---

## 10. Key risks & mitigations

| Risk | Mitigation |
|---|---|
| Plausible-but-wrong SQL (wrong join, wrong metric definition) | Canonical metric SQL in `metrics.md`, few-shot examples, execution-accuracy evals, show SQL + assumptions to the user. |
| Hallucinated numbers in the answer | Numbers computed in pandas; answer prompt must only cite values from `analysis`; faithfulness check in evals. |
| Data leakage / destructive queries | Read-only role + views + validator + RLS (defence in depth). |
| Slow/expensive queries | Read replica, `statement_timeout`, forced `LIMIT`, `EXPLAIN` cost check before run (v2). |
| Docs drifting from the real schema | Schema-drift check script in CI. |
| Ambiguous questions | Clarification branch in node 1 instead of guessing. |
