# Bookr Analyst: working tracker

**How we use this file:** at the start of every working session we go through **§2 Waiting on you** and **§1 Rules you gave us** together. Then we update the phase status. New rules or answers from the team go into §1 or §2 the moment they're given, with the date.

Last updated: 2026-11-19

---

## 1. Rules and requirements from the team

| # | Rule / requirement | Given | Where it lives in the code | Status |
|---|---|---|---|---|
| R1 | **Mandatory input fields.** Bookkr has required inputs it must collect before moving to the next step. If the agent asks for one of these fields, **including asking again**, the Friction Finder must **not** count that as friction. | 2026-11-19 | `config/friction.json` → `mandatory_fields`; also written into the LLM judge prompt | ✅ Built. ⚠️ The field list was **inferred from the data**, so please confirm it (§2 Q1). |
| R1a | How R1 is applied (our interpretation): the agent *asking* for a mandatory field is never flagged. The **user repeating information they had already given** is still flagged. That is the "user had to say it twice" signal, and it means intent extraction missed something. | 2026-11-19 | `friction/signals.py`, judge prompt | ⚠️ Please confirm (§2 Q2) |
| R2 | **Friction Finder runs over one day of chats.** It lists every friction item with a **link to the chat**, shows **where** in the chat it happened, **why** it was classified that way and **what type** it is. | 2026-11-19 | Friction Finder page (`/friction`) | ✅ Built |
| R3 | **Human review of every item:** *Agree* (goes into the dataset), *Not friction* (teaches the finder to ignore this pattern), or *Skip*. Then a **Review & submit** step. | 2026-11-19 | Review page `/friction/:id`, Dataset tab | ✅ Built |
| R4 | **Final output = a dataset file** that the Bookkr agent can use as RAG / few-shot context for better intent extraction. | 2026-11-19 | `GET /api/friction/export` → `data/exports/*.jsonl` + `.md` | ✅ Built. Format to confirm (§2 Q6) |
| R5 | **Recurring users report** with its own KPIs and dashboards. | 2026-11-19 | Users & retention page (`/users`) | ✅ Built |
| R6 | **Info (ⓘ) button** next to every KPI and field that explains what it measures. | 2026-11-19 | `frontend/src/glossary.ts` + `Info` component | ✅ Built |
| R7 | **Priority:** be ready to take **tomorrow's (2026-11-20) production data** and produce insights and the final file. | 2026-11-19 | n/a | ✅ 2026-11-20: on production data from 2026-11-02 (rules pass on all days; LLM judge from 19 Nov) |
| R8 | **Three friction buckets.** Friction Finder must separate **technical** friction (system broke), **intent** friction (Bookkr misread the user) and **agent-suggestion** friction (understood, but offered poor results / plans / answers / chips). Each item also gets the component that made the first wrong decision, which decides the fix: engineering, prompt, RAG or fine-tuning. Only LLM-owned items go to the dataset. | 2026-11-19 | `friction/taxonomy.py` (buckets, types, owners, lanes), `signals.py`, judge prompt and schema, `export.py` (4 files), Friction pages; design in `SYSTEM_MAP.html` | ✅ Built 2026-11-19. Running on Q10–Q13 defaults until you answer. |
| R9 | **Automated priorities, not manual review.** Reviewing every chat is too much work. Friction Finder must group friction into named issues, count how often each happens, rank them by priority per day, week and month, and give a business-analyst summary of what to fix first. Also a final **RAG export to improve intent capture**. Emails come later. | 2026-11-20 | `friction/issues.py` (register, priority, brief, RAG pack), `friction_api.py` (/report, /issues, /briefs, /rag-pack), Priorities tab (`FrictionReport.tsx`), issue page (`FrictionIssue.tsx`) | ✅ Built 2026-11-20. Emails: next |
| R0 | Never write to the source DB. Mask PII before storing it or sending it to an LLM. | earlier | `sync.py` (read-only), `masking.py` | ✅ |

## 2. Waiting on you (inputs needed)

| # | Question / input | Why it matters | Default we're using until you answer |
|---|---|---|---|
| Q1 | **Confirm the mandatory fields per flow.** We inferred them from the assistant's slot questions ("Could you share your departure city?", "what dates?", "who you're travelling with?"). | R1 depends on this list | Flight: origin, destination, departure date, return date (round trip only), passengers. Hotel: destination, check-in, check-out, guests. Trip plan / itinerary: destination, travel dates or month, trip duration, travelling with, departure city. The confirmation card ("Here is what I have so far…") always counts as expected. |
| Q2 | Is the R1a interpretation right? Example: the user says "BLR to Delhi on 15 Dec", the agent asks "Could you share your departure city?", and the user types "Bangalore" again. Our view: the *ask* is fine, but the user having to repeat "Bangalore" is friction (extraction missed it). | Decides what goes into training data | Flag the user's repetition, don't flag the ask |
| Q3 | **Production DB access:** a read-only role (`sql/create_readonly_role.sql`), ideally on a read replica. | Needed for tomorrow's data | ✅ 2026-11-20: `ops_readonly` on `bookkrdb`. Checked: SELECT on all 72 tables, no write, create, sequence or admin rights; its 3 security-definer functions only read. **Rotate its password** (shared in chat). |
| Q4 | **LLM for the Friction Finder judge**: `claude-sonnet-5-5` (2026-11-19: team asked to start with a lighter model than Opus; move to Opus only if review precision is low). 2026-11-19: the team asked to use the **Claude subscription** instead of an API key, and that's now supported. Install Claude Code on the machine that runs Bookr Analyst and log in with the Pro/Max account (`claude` → login). An API key (`ANTHROPIC_API_KEY`) still works as an alternative. | Without either, the finder runs **rules only** (template explanations, no suggested corrections) | ✅ 2026-11-19: Claude Code is installed and logged in; tested on a real chat (~25 s per chat) |
| Q5 | **Internal / test users.** Two user IDs have 137 and 138 sessions each (`cmviljst…` and `cmvk01sh…`, both flagged "likely team/test" on the Users page). Are these the team? Put their IDs in `.env → INTERNAL_USER_IDS`. | Otherwise they inflate the recurring-user and funnel numbers | `INTERNAL_EMAIL_DOMAINS=bookkrapp.com` set 2026-11-20; the two test-DB IDs don't apply to production. Name any other team accounts. |
| Q6 | **RAG file format.** Which Bookkr prompt will use the examples (`unified_intent_extraction`?), and in what shape: our JSONL, or a specific schema? | Decides the export format | JSONL (one example per line) + a Markdown version ready to paste into a prompt |
| Q7 | Are **guest user IDs** (`anonymous.bookkr.local`) stable for a returning device or browser? | Recurring-user numbers are only meaningful if they are | Assumed stable |
| Q8 | Does the privacy policy / user consent cover using conversations to improve the model (DPDP)? | Needed before production chats become training data | Personal names inside message text are **not** masked yet |
| Q9 | Reset the `neondb_owner` password, because it was shared in chat. | Security | n/a |
| Q10 | Is `INTENT_QWEN_LORA_ENABLED` on in production, and for which flows? | The LoRA gets a compact prompt, so RAG examples don't fit it: LoRA misses go to the fine-tune queue, gpt-4.1-mini misses go to RAG (R8) | Read it per turn from `LlmCallLog.model` |
| Q11 | Is the notification service (ADR 0004) live, or only planned? | Missing pushes count as technical friction only if it is live | Planned, not counted |
| Q12 | Does "suggestions from the agent" mean results, plans, chips and answers? Or does it also include clarifying questions? | Sets the line between the intent and suggestion buckets (R8) | Results, plans, chips and answers only. Questions stay in intent. |
| Q13 | Fine-tune trigger: 20+ agreed items in one guidance cluster, still failing the eval with RAG in place? | Sets how often a training run happens | 20 |

## 3. Phase status

| Phase | What | Status |
|---|---|---|
| 0 | Data foundation: masked sync from Neon into DuckDB | ✅ Done (test data) |
| 1 | Dashboards: Overview, Funnel, Booking ops, Conversations | ✅ Done |
| 1b | Users & retention dashboard, ⓘ info buttons everywhere | ✅ Done 2026-11-19 |
| 3 | **Friction Finder:** daily run, review queue, RAG dataset export | ✅ Built 2026-11-19 on test data. LLM judge works through the Claude subscription |
| 3b | **Three buckets (R8):** technical / intent / agent suggestion, owner and fix lane per item, four export files | ✅ Built 2026-11-19 on test data |
| 3c | **Priorities (R9):** issue register, P1/P2/P3 by users × severity, day/week/month trends, analyst brief, intent RAG pack, daily automation | ✅ Built 2026-11-20 on production data |
| 3d | Email the daily / weekly / monthly brief | ⏳ Next |
| 2 | Ask: text-to-SQL agent | ⏳ Next, after Friction Finder is live on production data |
| 4 | Scheduled reports (Slack/email), incremental sync for production volume | ⏳ Later |

## 4. Decisions log

| Date | Decision |
|---|---|
| 2026-11-19 | Friction Finder first (before Ask), so it is ready for production data on 2026-11-20. |
| 2026-11-19 | Retrieval (RAG / few-shot) first, not fine-tuning. Fine-tune only when retrieval stops improving the eval set. |
| 2026-11-19 | Reviews live in their own store (`data/friction.sqlite`), so a data refresh never wipes them. |
| 2026-11-19 | "Not friction" verdicts feed back into the judge prompt as known false positives, so it stops repeating them. |
| 2026-11-20 | Priorities are computed in SQL from item→issue links; Claude only names and groups issues and writes the brief. So every count on the page is exact and re-computable. |
| 2026-11-20 | Priority score = users hit × average severity; top 3 = P1, next 7 = P2. Kept simple on purpose; booking and money impact is weighed in the brief. Revisit once the team has a feel for it. |
| 2026-11-20 | The intent RAG pack needs no review: one rule per issue plus its 2–3 clearest examples (confidence ≥ 0.7). Agreed items rank first; "Not friction" and "Not an issue" are excluded. |

## 5. Checklist for switching to production (tomorrow)

1. Read-only production connection string goes into `.env → SOURCE_DATABASE_URL` (Q3), then **Refresh data**.
2. `INTERNAL_USER_IDS` for team/test accounts (Q5).
3. LLM judge (Q4): install Claude Code and log in with the Claude subscription (or set `ANTHROPIC_API_KEY`), then restart the server. The Friction Finder page shows "LLM judge on via Claude subscription" when it's picked up.
4. Archive the test-data review queue: `cd backend; .\.venv\Scripts\python.exe -m bookr_analyst.friction --reset`.
5. Friction Finder: pick the production day → **Run analysis** → review → Dataset & export → **Generate file**.
6. Optional: `FRICTION_DAILY_AT=09:00` so it runs every morning.

## 6. Session log

- **2026-11-20 (2):** Built **Priorities** (R9) so the team reads a ranked list instead of reviewing chats.
  - Issue register: Claude groups each judged item into a named root cause (e.g. "Extractor sets student fare type without the user asking"), with theme, owner, fix lane, recommended fix and a prompt rule. Status per issue (Open / Being fixed / Fixed / Won't fix / Not an issue); fixed issues that come back are flagged "Back after fix".
  - Priorities tab (now the default): Day / Week / Month, analyst brief, KPIs, P1/P2/P3 issue table with trend vs previous period, product-area chart, friction-type table, gone-quiet and closed-but-seen lists, RAG pack card. Issue page per issue.
  - Automation: `FRICTION_DAILY_AT=07:00` → sync, judge yesterday, group, daily brief, weekly brief after Sunday, monthly brief at month end, refresh RAG pack. Needs the server running at 07:00.
  - First weekly brief (16–22 Nov, 1 of 4 days judged): top issue #18 (student fare type invented, 15 users, searches return 0 results).
  - Intent RAG pack: `data/exports/bookkr_intent_rag_pack_*.jsonl` + `.md` (23 examples / 14 issues from 19 Nov; grows with the backfill).
  - LLM judge backfill running for 2–18 Nov, newest first (`backend/scripts/backfill_judge.py`). When it ends, press "Group N new items into issues" on Priorities (or wait for the 07:00 run).

- **2026-11-20 (1):** Switched to **production**.
  - Connection: `.env → SOURCE_DATABASE_URL` is `ops_readonly@bookkrdb`. The test URL is kept, commented out.
  - New `.env → SYNC_SINCE=2026-11-02`: only data from 2 Nov 00:00 IST is copied (`sync.py SINCE_COLUMN`). Chats and bookings filter on `updatedAt`; users are copied in full.
  - Backups: `data/analytics.test-uat-backup.duckdb`, and the test review queue was archived to `data/friction.archived-20261120-010452.sqlite` (checklist step 4).
  - Sync took 240 s: 6,564 chats, 58,114 messages, 31,130 intents, 12,532 flight searches, 631 bookings, 68,638 LLM calls, 13,718 analytics events.
  - Friction Finder rules-only pass on 2–19 Nov: 6,163 chats scanned, 1,399 flagged. LLM judge started on 19 Nov; other days are still rules-only.
  - The server now runs in a Terminal tab, because the app was stopping the preview server.

- **2026-11-19 (5):** Applied the three judge fixes and re-ran **2026-11-01**.
  - What changed:
    - Two new fixed-rule owners: `plan_turn_rules` and `flight_state_carryover`.
    - A new `intent_drift` signal (weight 2), which fires when the `flight_search` intent changes a city from the `travel_intent` just captured.
    - The judge prompt now covers both traps and asks for one item per root cause.
  - The re-run: 16 chats flagged (was 10), 21 items.
    - All three owner problems from the first run are fixed.
    - Duplicates are merged.
    - 6 more chats were found with the same carried-state bug: a reversed or swapped route, such as DEL→BLR asked and BLR→DEL searched.
  - Across all 30 test days the new signal fires in 10 chats:
    - 5 with origin and destination reversed on a one-way trip.
    - 5 with the user's origin replaced by BLR.
  - Only 2 items now go to the RAG file (the Kanpur dates and the dropped destination), so no fixed-rule bug leaks into training data.
  - Lost: the low-confidence "Delhi/Bangkok kept only in notes" item from the first run.
  - Still Claude's check, not team verdicts.

- **2026-11-19 (4):** LLM judge precision check on test day **2026-11-01**.
  - The run: 36 chats scanned, 12 sent to the judge, 10 flagged, 16 items, 70 s on the subscription.
  - Claude read every item against its transcript. These are not team verdicts; nothing was written to the review store.
  - Results:
    - 16/16 are real friction, with 1 duplicate of the same root cause.
    - Bucket right: 16/16.
    - Owner right: 13/16, with 2 uncertain and 1 wrong.
    - The wrong one: "show me flights" with plans on screen went to plans because of a fixed turn rule, not the LLM router. It would have gone into the RAG file.
  - Judge fixes proposed:
    - Add a "plan turn rules" owner.
    - Explain the second `flight_search` extraction to the judge, and add a signal for when it disagrees with `travel_intent`.
    - One item per root cause.
  - Product findings for the Bookkr team:
    - The flexible-month date-price calendar failed in 3 chats.
    - One chat showed "Booking confirmed" and then the booking failed (payment voided).
    - A Hyderabad→Jaipur search ran as BLR→JAI.
    - 4 chats hit "multi-route not supported".

- **2026-11-19 (3):** Built R8.
  - Every item now has a **bucket**, a **type**, an **owner** (the LLM task, fixed rule or service that made the first wrong decision), the **model that served the turn** (from `LlmCallLog`) and a **fix lane**: engineering, prompt, RAG, fine-tune, product or triage.
  - **Sync:** new `raw_analytics_events` table, with only these events: payment failed/dismissed, price changed, fare expired, unsupported feature, results shown, and chip/filter/sort use. 305 rows on test data.
  - **New signals:**
    - Technical: error replies, LLM failures the user saw (a failure the fallback recovered from is context only), failed bookings (an abandoned payment is context only), failed payments, expired fares, slow replies (40 s or more).
    - Agent suggestion: product-gap replies and requests, filter/sort changes after results.
    - Intent: a search that differs from the extracted intent (return legs and plan legs are handled).
  - **Judge:** prompt and schema now cover all three buckets, the owner and "lesson is a rule".
  - **Export:** four files. Intent RAG, response quality, fine-tune queue (LoRA-served misses), and an engineering/product/triage list with chat links.
  - **UI:** bucket filter cards, bucket and lane on every chat, a routing panel on each review item, the four files plus fine-tune candidates and LoRA failure stats on the Dataset tab, and the taxonomy on the Rules tab.
  - **Verified:**
    - Rules mode on all 30 test days: 79 items across all buckets.
    - A real LLM-judge run on one chat (29 s). The judge put a past-date rule bug in Engineering, two Nemotron-served extractor misses in the fine-tune queue, and a missing results card in Technical.
    - The reviewer override API.
    - The four-file export, on a scratch copy of the store.
  - **Backup:** the review store from before this change is at `data/friction.backup-pre-r8.sqlite`.
  - **Fine-tune signal:** test data shows the Nemotron LoRA served 263 extractions, with schema-parse failures.

- **2026-11-19 (2):** Read the UAT code base zip (`Code Base/bookkr-combined-uat.zip`). Wrote `SYSTEM_MAP.html`: system architecture, the chat-turn pipeline (LLM steps vs fixed rules vs outside services), all 13 LLM tasks, booking/payment flow, and the two sorting trees (bucket, then fix lane). Added R8 and Q10–Q13. Findings: many "misunderstood" moments are decided by fixed rules after the LLM, so the judge must compare user words, extractor output and search params. `AnalyticsEvent` (payment, fare, chips, filters, platform) is not synced yet and holds most technical signals. No Friction Finder code changed yet.

- **2026-11-19:** Built the Friction Finder (rule signals, optional Claude judge, review queue with Agree / Not friction / Skip, edits, submit, JSONL + Markdown export), the Users & retention page, ⓘ info buttons on every KPI, and the user filter on Conversations. Created this tracker. Verified on test data: 25 chats flagged across all days, the review → submit → export flow works in the browser, and the LLM path was tested with a stand-in client. **Not yet tested against the real Claude API** (no key, and this machine couldn't reach api.anthropic.com during the session). The test-data review queue still holds rules-only findings; reset before production (step 4 above).
