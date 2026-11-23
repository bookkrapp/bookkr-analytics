# Bookr Analyst Platform — Vision & Roadmap

An internal web platform for the 4-person team. It has **dashboards** you open and read, a **chat page that answers questions by writing SQL**, a **conversation debugger**, and a **"friction finder"** that mines production chats for intent failures and turns them into training data for Bookkr's assistant.

This builds on [ARCHITECTURE.md](ARCHITECTURE.md) (the SQL agent), [DATA_DICTIONARY.md](DATA_DICTIONARY.md) and [REPORTING_USE_CASES.md](REPORTING_USE_CASES.md).

---

## 1. What the platform contains

| Module | What it's for | Who uses it |
|---|---|---|
| **Overview** | One-page health of the business: users, chats, searches, bookings, GMV, failures. Week-over-week change plus an AI-written "what changed" summary | Everyone, daily |
| **Dashboards** | Funnel · Booking & payment ops · Demand & destinations · Assistant quality · Engagement & retention · Revenue · Supplier reliability · LLM cost | Product, ops, growth |
| **Alerts** | Rules that flag problems (stuck payments, Tripjack failure spike, searches returning nothing) and push to Slack/email | Ops, engineering |
| **Ask** | A chat box: ask a question in plain English and get the SQL, a result table, a chart and an explanation. Answers can be saved as a dashboard tile | Everyone |
| **Conversation Explorer** | Open any chat as a timeline: user message → extracted intent → flow state → search → results → booking. Filter by "failed", "stuck", "booked" and so on | Product, engineering (debugging) |
| **Friction Finder** | Automatically finds turns where the assistant didn't capture or keep what the user said. Ranks them by type and frequency | Product, ML |
| **Training Data Studio** | A review queue: approve or correct the "what the extraction should have been" label, then export few-shot examples and fine-tuning JSONL. It also holds the **eval set** | ML / whoever owns the intent model |

---

## 2. Architecture

```
 Production Neon (app DB)
        │  read replica + read-only role (PII-masked views)
        ▼
 ┌──────────────────────────┐   hourly/nightly sync (Python job)
 │  Analytics DB (separate  │◄─────────────────────────────────────┐
 │  Neon project / branch)  │                                      │
 │  raw_* copies (masked)   │   derived tables:                    │
 │  + derived tables        │   session_facts, funnel_events,      │
 └──────────┬───────────────┘   conversation_turns, intent_diffs,  │
            │                   friction_events, labels, report_runs
            ▼                                                       │
 ┌──────────────────────────┐        ┌──────────────────────────┐   │
 │  FastAPI backend          │──────►│  LangGraph services       │──┘
 │  /metrics /dashboards     │       │  • Ask (text-to-SQL)      │
 │  /ask (stream) /sessions  │       │  • Friction judge (batch) │
 │  /friction /labels        │       │  • Report narrator        │
 └──────────┬───────────────┘        └──────────────────────────┘
            ▼                                   Claude (claude-opus-5-5)
 ┌──────────────────────────┐
 │  Web UI (React + Vite,    │   runs locally first → deploy later
 │  charts, tables, chat)    │   (Render/Fly/Vercel + login)
 └──────────────────────────┘
```

**Key decisions:**
- **Separate analytics database.** Heavy queries and LLM batch jobs never touch production. Derived tables (and our labels) need somewhere writable anyway, and the production role stays read-only.
- **Mask PII on the way in.** Emails, phones, names, passport/GST details and raw traveller JSON are removed or hashed during the sync. Message text gets entity masking (phone/email/names) before it is stored or sent to an LLM.
- **Dashboards run fixed, reviewed SQL.** Only the **Ask** page writes SQL on the fly. This keeps dashboard numbers trustworthy.
- **Stack:** Python everywhere (FastAPI, LangGraph, pandas) plus a React front end. It is a little more work than Streamlit, but it handles the chat, the explorer timeline and the labeling queue properly, and it can be deployed with login later. *(If you'd rather see something in days, a Streamlit v0 is possible, but we'd likely rewrite it.)*

---

## 3. Friction Finder: catching intent failures (the training-data engine)

### 3.1 What counts as "friction"
Each signal can be detected from the data we already have (`Message`, `ExtractedIntent`, `UnifiedChatFlowState`, search tables, `AssistantMessageFeedback`):

| # | Signal | How we detect it |
|---|---|---|
| F1 | **Repeated information**: user gives the same slot again (destination, dates, passengers, origin) | Compare `ExtractedIntent.parameters` across consecutive user turns. The same slot value appears again after the assistant asked for it or ignored it. Plus semantic similarity (embeddings) between user messages in the same chat |
| F2 | **Re-explanation**: user says the same thing in more detail because the first attempt wasn't understood | High similarity with the previous user message *and* the new message is longer, *and* the intent/flow didn't advance |
| F3 | **Slot ping-pong**: assistant asks for something the user already gave | The assistant's question mentions slot X, and slot X was present in an earlier user message's extraction |
| F4 | **Corrections**: "no, I meant…", "not Delhi, Dubai", "I already told you" | Keyword/regex for correction and frustration phrases (English + Hinglish), confirmed by an LLM |
| F5 | **Confirmation rejected**: `intent_confirmation` card followed by the user changing parameters | Card sequence in `Message.metadata.card.type` |
| F6 | **Wrong search**: search parameters ≠ what the user asked (wrong date, airport or passenger count) | Diff `FlightSearchRequest` / `HotelSearchRequest` against the latest extracted intent and the user's words |
| F7 | **Stuck flow**: `currentFlow = unresolved` after ≥ N user messages, or no search after N turns | `UnifiedChatFlowState` + message counts |
| F8 | **Abandonment after a question**: the last message is an assistant clarification and the user never replied | Session-end analysis |
| F9 | **Explicit negative feedback** / superseded messages / `shared_failure` events | `AssistantMessageFeedback`, `metadata.supersededBy`, `Message.kind` |
| F10 | **Unsupported demand**: user asks for car rental, activities, trains, visas… | Intent enum values plus LLM classification. **Product roadmap input**, not a model bug |

### 3.2 Pipeline
```
1. Candidate generation (SQL + Python rules, cheap, runs nightly over new chats)
2. LLM judge (Claude, batched): for each candidate, read the masked conversation up to that turn and output:
     { is_failure, failure_type, missed_slots, what_user_meant,
       model_extraction (actual), correct_extraction (proposed, same JSON schema as ExtractedIntent.parameters),
       severity, root_cause_guess: extraction | memory/retention | flow logic | supplier | UX }
3. Human review in Training Data Studio: approve / edit / reject (keyboard-driven, fast)
4. Approved items → three outputs:
     a) few-shot library (embedded, retrievable by similarity)
     b) eval set (frozen regression test for the intent extractor)
     c) fine-tuning JSONL (when volume is big enough)
```

### 3.3 Few-shot now, fine-tune later
1. **Dynamic few-shot first.** At inference time, the production intent extractor retrieves the 3–8 most similar approved examples (by embedding similarity) and puts them in its prompt. This avoids stuffing one huge static list in, scales to thousands of examples and needs no retraining.
2. **Measure everything against the eval set.** Every prompt or model change is scored on the frozen eval set: slot accuracy, intent accuracy and "would the user have had to repeat themselves?" rate. That number tells us whether fine-tuning is worth it.
3. **Fine-tune when** few-shot plateaus *and* there are enough reviewed examples (typically a few thousand clean, diverse ones). Which fine-tuning route is available depends on the model the production assistant uses, which we need to confirm.
4. Root cause matters. Many "repeat yourself" failures are **memory/state bugs** (a slot extracted correctly but lost from `UnifiedChatFlowState`), not extraction mistakes. Fine-tuning won't fix those, but the Conversation Explorer will show them.

### 3.4 Privacy & consent (production data)
- Mask PII before storing or sending conversations to any LLM.
- Check that Bookkr's privacy policy/terms allow using conversations to improve the service and train models (India's DPDP Act 2023 applies). Keep a deletion path: if a user is deleted, their examples are removed from the training set.

---

## 4. Everything else this data can do

### Grow the business
- **Lost-demand recovery:** routes and dates searched but never booked → fare-drop alerts and re-engagement via the existing `bookkr_notifications` campaigns.
- **Demand map:** top routes, hotel cities, lead times and trip lengths by week → which routes to promote, what content to make, which suppliers or deals to negotiate.
- **Price sensitivity:** prices shown (`FlightOption`/`HotelOption`) vs prices booked → where users drop off on price, and how platform fees affect conversion.
- **Group trips:** group-chat usage, size, conversion vs solo → is the group feature driving bookings?
- **Unsupported demand (F10):** quantified requests for cars, activities, trains → roadmap evidence.
- **Retention cohorts:** do bookers come back? How long until the second trip?

### Improve user experience
- Time to value: messages and minutes from the first message to the first search result.
- Where in the flow users drop off (the funnel by card type).
- Clarification load: questions asked per booked trip.
- Friction rate per 100 chats (from §3), tracked weekly as a **north-star quality metric**.

### Find bugs
- Bookings stuck in `PENDING_BOOK` / `VOID_PENDING` / `CAPTURED_AWAITING_PROVIDER` (money at risk).
- Tripjack error codes and failure-rate spikes; searches with no result row (36% today).
- Duplicate searches (same `searchHash` within seconds → retry or UI bug).
- Booked but no confirmation email; `ticketedAt` never set.
- Flow state inconsistencies: a slot in the intent but missing in the flow state, and sessions stuck in `unresolved`.
- **LLM ops** (`LlmCallLog`): latency, cost, errors and timeouts per call type, and cost per booking.

### Create new data
- Intent-extraction training and eval sets (§3).
- A **clarification-question dataset**: good vs bad assistant questions.
- **Search-ranking signals**: which results users chose vs were shown → future ranking model.
- A canonical **destination normalisation** table ("Goa, India" / "South Goa" / "Palolem" → hierarchy).
- An auto-tagged conversation corpus (topics, trip types, sentiment) for product research.

---

## 5. Tables to add beyond the current 9 (to profile next)
`User` (signup date, internal/test flag) · `AssistantMessageFeedback` (explicit thumbs, gold for training) · `LlmCallLog` (LLM cost/latency/errors) · `AnalyticsEvent` (app events, page funnel) · `FlightOption` / `HotelOption` (what users were shown) · `Booking` / `HotelBookingState` (hotel bookings) · `payments.PaymentOrder` / `PaymentTransaction` / `Refund` · `SupportTicket` (+ messages) · `HotelChatFlowState` · `ChatShortlistItem` / `ChatPoll` (group decisions) · `bookkr_notifications.*` (campaign effect).

---

## 6. Roadmap

| Phase | Weeks | Deliverables |
|---|---|---|
| **0. Data foundation** | 1 | Read-only role on a prod read replica, PII-masked views, analytics DB, sync job, test-user filter, timezone decision |
| **1. Platform v1** | 1–2 | Web app skeleton (local), **Overview**, **Booking & payment ops** + **Funnel** dashboards, **Conversation Explorer** |
| **2. Ask** | 1–2 | Text-to-SQL agent in the UI (from ARCHITECTURE.md), save-as-tile, query audit log |
| **3. Friction Finder + Studio** | 2–3 | Signals F1–F10, LLM judge, review queue, few-shot export, eval set v1 |
| **4. More dashboards + alerts** | 1–2 | Demand, Assistant quality, Engagement, Revenue, Supplier, LLM cost; Slack/email alerts; weekly AI digest |
| **5. Close the loop** | ongoing | Plug dynamic few-shot into the production intent extractor, track the friction rate, fine-tune when volume justifies it; deploy with login |

Roughly **6–10 weeks** for one developer to finish Phases 0–4. A useful v1 (Phases 0–1) arrives in about 2–3 weeks.

---

## 7. Decisions needed from the team
1. **Production access:** can we get a read replica + read-only role, and permission to sync masked copies into a separate analytics DB?
2. **The production intent extractor:** which model/provider does it use, and can we access its code/prompt? This decides how few-shot is injected and which fine-tuning route exists.
3. **Privacy:** does the current privacy policy cover using conversations for model improvement?
4. **Test/internal users:** how do we identify them (email domain list, flag on `User`)?
5. **Login:** local-only for now, and Google sign-in limited to the company domain when deployed?
6. **First priorities:** confirm Phases 0–1 first (ops alert + funnel + explorer), or move the Friction Finder earlier.
