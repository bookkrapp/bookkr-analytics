# Bookr Analyst — Scheduled Reporting: Use Cases & Approach

Based on the data in [DATA_DICTIONARY.md](DATA_DICTIONARY.md). This is a shortlist to choose from, not a final spec.

## 1. Agent or workflow? Recommendation: both, with different jobs

| | Scheduled report **workflow** | Ad-hoc **analyst agent** (ARCHITECTURE.md) |
|---|---|---|
| Trigger | Cron (daily / weekly) | A person asks a question |
| SQL | **Fixed, reviewed queries** per report | LLM writes SQL on the fly |
| LLM role | Writes the narrative: "what changed, why it matters, what looks odd" | Writes the SQL, analyses the results, answers the question |
| Why | Numbers must be identical and trustworthy every run | Flexibility for questions nobody planned for |

Recurring reports should **not** have the LLM invent SQL on each run. If the queries are fixed, the numbers are reproducible and comparable week to week, and the LLM adds the explanation on top. When the weekly report raises a question, you ask the ad-hoc agent.

Both share the same pieces: the read-only Neon role, the `context/` MD files, the LangGraph runtime and the Claude model.

```
cron ─► LangGraph "report" graph
          ├─ run_metric_queries   (fixed SQL files: reports/<name>/*.sql)
          ├─ compute_deltas       (pandas: vs last period, vs 4-week avg)
          ├─ detect_anomalies     (rule thresholds, e.g. VOID_PENDING > 0)
          ├─ write_narrative      (Claude: summary + highlights + risks)
          ├─ render               (Slack blocks / HTML email / PDF)
          └─ deliver + log        (Slack webhook / email; store run in report_runs)
```

## 2. Candidate reports (ranked by value given today's data)

### A. 🔴 Booking & payment health alert (daily, alert-only)
*Why first:* the data already shows **6 failed bookings worth ₹2.8L stuck in `VOID_PENDING`** and 2 captured-but-awaiting-provider. These are customers who may have paid without getting a ticket.
- Bookings `FAILED` with `paymentStatus` in (`CAPTURED`, `VOID_PENDING`, `CAPTURED_AWAITING_PROVIDER`) older than N hours
- `PENDING_BOOK` / `PENDING` older than 30 min
- `BOOKED` but `confirmationEmailSentAt` is null
- Tripjack error count by `failureReason` (last 24h)
- **Sends only when something needs action.** Goes to an ops channel.
- Tables: FlightBooking (+ `payments.PaymentOrder` later)

### B. 📈 Weekly conversion funnel (weekly, Monday)
- Sessions → user message → intent confirmed → flight/hotel search → results shown → booking attempted → booked, with week-over-week change
- Drop-off per stage, and the biggest leak called out by the LLM
- Split by flow: `single_flight` vs `multi_city` vs `unresolved`
- Tables: ChatSession, Message (`metadata.card.type`), UnifiedChatFlowState, FlightSearchRequest, HotelSearchRequest, FlightBooking

### C. ✈️ Demand & destinations (weekly)
- Top searched routes, top hotel destinations (city names normalised), new routes this week
- Lead time (search → departure), trip length, one-way vs round-trip, group size
- Searched-but-never-booked routes (an input for marketing and pricing)
- Tables: FlightSearchRequest, HotelSearchRequest, ExtractedIntent.parameters

### D. 🤖 Assistant quality / conversation health (weekly)
- Share of sessions stuck in `unresolved` flow (112 of 322 today, a big signal)
- Sessions that never reached a search, clarification loops, low-confidence intents
- Searches with **no result row** (36% today), `shared_failure` / `system_event` messages
- Messages per session before the first search ("time to value")
- Optional LLM pass: classify *why* unresolved sessions stalled from a **masked** sample of messages
- Tables: UnifiedChatFlowState, ExtractedIntent, Message, FlightSearchRequest/Result

### E. 👥 User engagement & retention (weekly / monthly)
- Active users (daily/weekly), new vs returning, sessions per user, group-chat usage
- Cohort: users who come back within 7/30 days
- Needs `User` table (signup date, internal flag)

### F. 💰 Revenue snapshot (weekly, once real traffic exists)
- GMV (`amountPayable` of BOOKED), average booking value, wallet usage share, platform fee (`pricingRules`)
- Later: hotel bookings (needs `Booking` / `HotelBookingState`), refunds (`payments.Refund`)

**Suggested start:** **A** (daily alert) + **B** (weekly funnel). C and D come next. E and F wait until real-user traffic grows and the test-user filter exists.

## 3. Delivery & scheduling options

| Option | Notes |
|---|---|
| Slack incoming webhook | Simplest, good for alerts (A) and short weekly summaries |
| Email (HTML) via SES/Resend | Better for longer weekly reports with tables and charts |
| Scheduler | Render/Railway cron, GitHub Actions `schedule`, or LangGraph Platform cron jobs. All run the same Python entry point |
| Run log | `report_runs` table (in a separate writable schema): run time, report, status, metrics JSON. This allows week-over-week comparison and auditing |

## 4. Prerequisites (from DATA_DICTIONARY §4)
1. A read-only role plus PII-free views over these tables.
2. A rule for excluding test/internal users.
3. Timezone confirmation (UTC → IST).
4. Agreed definitions: "booked", "active user", "session reached search".
5. Where reports go (Slack channel / email list) and who owns each report.
