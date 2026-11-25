# Bookr Analyst — Data Dictionary (chat → search → booking tables)

Snapshot taken **2026-11-18** from Neon database `neondb`, schema `public` (PostgreSQL 17).
Profiling used only read-only queries. No rows were changed.

> **About the descriptions.** The database has **no column comments**, so every description below is inferred from the column name, its type, its constraints and the actual values in it. Entries marked *(verify)* are best guesses. Someone from engineering should confirm them before they become the agent's `context/` files.

---

## 1. Big picture

Bookkr is a **conversational travel-booking app**. A user chats with the assistant ("Bookkr"). The assistant works out the trip intent, runs flight and hotel searches, shows result cards, and books flights through the **Tripjack** API. Hotels are searched via **Google Hotels (SerpAPI) + Tripjack**.

```
User ──1:N──► ChatSession ──1:N──► Message ──1:N──► ExtractedIntent
                  │  1:1                                   (what the user wants)
                  ├──► UnifiedChatFlowState   (where the conversation is in the flow)
                  ├──1:N──► FlightSearchRequest ──1:N──► FlightSearchResult ──1:N──► FlightOption*
                  ├──1:N──► HotelSearchRequest  ──1:N──► HotelSearchResult  ──1:N──► HotelOption*
                  └──1:N──► FlightBooking (via chatSessionId, no FK) ──► FlightBookingTraveller*, FlightBookingSeat*, FlightAmendment*
```
`*` = a related table outside the 9 you selected. Note that `FlightSearchResult` holds **no flight details itself**. The actual fares and itineraries are in `FlightOption`, and the hotel list per search is in `HotelOption` (and also inside `HotelSearchResult.rawResponse`).

### Volume & time range

| Table | Rows | Size | Data from → to |
|---|---:|---:|---|
| ChatSession | 326 | 60 MB | 2026-07-24 → 2026-11-17 |
| Message | 2,139 | 68 MB | 2026-07-24 → 2026-11-17 |
| ExtractedIntent | 1,075 | 1.9 MB | 2026-07-24 → 2026-11-17 |
| UnifiedChatFlowState | 322 | 1 MB | 2026-07-24 → 2026-11-17 |
| FlightSearchRequest | 371 | 0.3 MB | 2026-07-24 → 2026-11-16 |
| FlightSearchResult | 237 | 0.1 MB | 2026-07-24 → 2026-11-16 |
| FlightBooking | 57 | 0.5 MB | 2026-07-24 → 2026-11-16 |
| HotelSearchRequest | 113 | 0.1 MB | 2026-10-22 → 2026-11-17 |
| HotelSearchResult | 113 | 8 MB | 2026-10-22 → 2026-11-17 |

**Only 43 distinct users** in total. There was almost no activity between mid-August and mid-October, and a spike in the weeks of 19 and 26 October (≈1,800 messages). This looks like **beta/internal testing**. Before any report goes out, decide whether internal or test accounts should be excluded (see §4).

### Conventions in this schema
- All IDs are `text` (Prisma cuid/uuid-style). Table and column names are **camelCase and must be double-quoted** in SQL: `"ChatSession"."userId"`.
- Timestamps are `timestamp without time zone`. Values are most likely UTC *(verify)*; convert to IST for reports.
- All money is **INR** (the column defaults say `USD`, but every row is `INR`).
- Soft deletes: `Message.deletedAt` and `ExtractedIntent.deletedAt`. Reports should filter `"deletedAt" IS NULL`.
- Much of the useful detail is inside **JSONB** columns. Their top-level keys are listed below.

---

## 2. Tables

### 2.1 `ChatSession`: one conversation/trip-planning thread
What it stores: a chat between a user (or a group) and the assistant. It is the parent of almost everything else.

| Column | Type | Description |
|---|---|---|
| id | text PK | Session ID |
| userId | text FK→User | User who created the chat |
| title | text | Chat title shown in the app (likely auto-generated) |
| isActive | bool | Whether the chat is open (325 true / 1 false) |
| latestExtractedIntentId | text | Pointer to the most recent `ExtractedIntent` (28% null = no intent yet) |
| lastMessageId | text | Pointer to the last `Message` (for chat-list previews) |
| createdAt / updatedAt | timestamp | Created / last activity |
| chatType | text | `unified` (323), the current combined flight+hotel flow; `standard` (3), legacy |
| panelCards | jsonb array | Result panels shown beside the chat. Each item: `id, type, label, summary, payload, createdAt, initiatedByUserId, sourceMessageId, options…`. Types: `flight_results` 162, `hotel_results` 57, `trip_plan_options` 52, `multi_city_flights` 16, `multi_city_hotel_results` 5 |
| latitude / longitude | float | User's location when the chat started (60% filled; all in India) |
| preferredCurrency | text | Unused (100% null) |
| hotelShortlist | jsonb | Unused (100% null). Shortlists live in `ChatShortlistItem` |
| creationRequestId | text unique | Idempotency key for chat creation (mostly null) |
| isGroupChat | bool | Group trip chat (9 sessions) |
| description, groupPhoto | text | Group-chat metadata (unused so far) |
| groupInfoVersion | int | Version counter for group info edits (all 0) |
| preferenceVersion | int | Number of times trip preferences were edited (avg 1.9, max 14) |
| preferenceEditorUserId / Name / ExpiresAt | text/ts | Lock showing who is editing preferences in a group chat (unused so far) |

### 2.2 `Message`: every chat message
What it stores: user, assistant and system messages, plus the "card" (rich UI element) attached to the message.

| Column | Type | Description |
|---|---|---|
| id | text PK | Message ID |
| chatSessionId | text FK→ChatSession | Parent chat |
| userId | text FK→User | Sender (for assistant/system messages, likely the session owner *(verify)*) |
| role | enum `MessageRole` | `USER` 830, `ASSISTANT` 1,223, `SYSTEM` 86 |
| content | text | Message text. **May contain personal details users type; treat as PII** |
| metadata | jsonb | Keys: `card` (rich card, see below), `bookkrInvoked` (true when the user explicitly called the assistant, e.g. in group chats *(verify)*), `clientMessageId`, `hotelCard`, `intentConfirmation`, `systemNotice`, `supersededBy`, `groundingCategory`, `citations` |
| kind | text | Special message kinds (97% null): `flight_date_strip`, `shared_failure`, `system_event`, `decision_recommend` |
| clientMessageId | text | Client-side de-duplication ID |
| createdAt / updatedAt | timestamp | |
| deletedAt | timestamp | Soft delete (11 rows) |

`metadata.card.type` values: `flight_results` 277, `intent_confirmation` 231, `itinerary_options` 83, `payment_result` 34, `itinerary_dates` 32, `flight_flex_strip` 23, `hotel_booking_result` 16, `hotel_card` 12, `flexible_calendar` 9, `flight_date_strip` 3, `command_card` 2.
**These card types are the best funnel markers in the chat:** intent confirmed → results shown → payment result.

Typical session: median 5 messages, p90 12, max 34.

### 2.3 `ExtractedIntent`: what the AI understood from a user message
What it stores: the output of the NLU/LLM step for a message. That is the intent type, a confidence score and structured trip parameters.

| Column | Type | Description |
|---|---|---|
| id | text PK | |
| messageId | text FK→Message | Message this intent was extracted from |
| chatSessionId | text FK→ChatSession | |
| intentType | enum `IntentType` | Values in use: `travel_intent` 708, `flight_search` 187, `hotel_search` 167, `multi_city_trip_planning` 13. (Enum also allows car_rental_search, activity_search, modify_search, view_details, confirm_booking, cancel_booking, payment_info, user_preference, clarification_needed, other; none used yet) |
| confidence | float 0–1 | Model confidence (avg 0.95; only 4 rows < 0.7) |
| parameters | jsonb | Structured trip details. Flight keys: `origin, destination, departure, return, passengers, cabin, tripType, dateMode, budget/budgets, flightPreference, departureAirportSelection`. Trip-planning keys: `travelWindow, travelStyle, paceOfTrip, groupType, travellingWith, interests, vibePreferences, cityPreferences, regionPreferences, accommodationPreference, exclusions, isDestinationFlexible, notes, actionHint`. Hotel keys: `propertyName, checkInDate, hotelClass, minPricePerNight, freeCancellation, amenityAvoided, eventReference`. `destination` is either a string or an object `{name, iata, type, countryCode, countryName}` |
| rawText | text | Unused (100% null) |
| createdAt / updatedAt / deletedAt | timestamp | |

> Note: two naming styles exist (`tripType` vs `trip_type`, `dateMode` vs `date_mode`), which shows the schema changed over versions. Queries must handle both.

### 2.4 `UnifiedChatFlowState`: the state machine of each chat (one row per session)
What it stores: where a conversation currently stands in the booking flow. It is overwritten as the chat moves forward, so it is a **current-state snapshot, not history**.

| Column | Type | Description |
|---|---|---|
| id | text PK | |
| chatSessionId | text FK, unique | 1:1 with ChatSession |
| currentFlow | enum `UnifiedFlowType` | `single_flight` 149, `unresolved` 112 (never got to a clear flow), `multi_city` 61 |
| flightState | jsonb | Single-flight flow: `status` (`intent_ready` 155, `needs_clarification` 1), `entities`, `confirmedSearch`, `awaitingConfirmation`, `combination`, `latestIntentId`, `originContextCity`, `redisKey`, `expiresAt` |
| multiCityState | jsonb | Multi-city flow: `phase` (`itinerary_generated` 34, `flights_shown` 17, `dates_generated` 10), `status`, `tripProfile`, `tripPlanningInputSchema`, `selectedOptionKey`, `exactDatePair`, `selectedStayWindow`, `travellerCount`, `pendingPlanQuestion`, `pendingDatesRequest`, `pendingHotelFanout`… |
| travelIntent | jsonb | Latest merged trip intent (same shape as `ExtractedIntent.parameters`). `tripType`: ONE_WAY 106 / ROUND_TRIP 37 / not set 147 |
| createdAt / updatedAt | timestamp | |

### 2.5 `FlightSearchRequest`: each flight search sent to the supplier

| Column | Type | Description |
|---|---|---|
| id | text PK | |
| chatSessionId | text FK (set null on delete) | Chat that triggered it |
| userId | text FK→User | |
| originCode / destinationCode | text | IATA airport codes. Top routes: BLR→DEL 112, DEL→BLR 71, BLR→KNU 28, BLR→UDR 26, BLR→JAI 15 |
| departureDate / returnDate | timestamp | Travel dates (return 87% null = mostly one-way) |
| adults / children / infants | int | Passenger counts (avg 1.08 adults; no children/infants yet) |
| cabinClass | enum `CabinClass` | All `ECONOMY` so far |
| currencyCode | text | All `INR` |
| searchHash | text | Hash of the search parameters, used for caching and de-duplication. Repeated searches share a hash |
| isRoundTrip | bool | 49 true / 322 false |
| createdAt / updatedAt | timestamp | When searched |

Lead time from search to departure: p25 6 days, **median 22 days**, p75 34 days.

### 2.6 `FlightSearchResult`: a result set returned for a search

| Column | Type | Description |
|---|---|---|
| id | text PK | |
| searchRequestId | text FK→FlightSearchRequest | |
| resultGroup | int | Result grouping index (always 0 so far) |
| relevanceScore | float | Always 0.5 (placeholder) |
| createdAt / updatedAt | timestamp | |

Only **237 of 371 searches (64%) have a result row**. This may mean searches with no results or that errored, or results served from cache *(verify)*. The individual fares are in `FlightOption`.

### 2.7 `FlightBooking`: flight booking attempts via Tripjack
What it stores: one row per booking attempt, from fare lock through payment to ticketing.

| Column | Type | Description |
|---|---|---|
| id | text PK | |
| userId | text FK→User | Booker |
| provider | text | `tripjack` (all) |
| providerBookingId | text | Tripjack booking reference |
| pnr | text | Airline PNR (only present for successful bookings) |
| ticketNumbers | text[] | E-ticket numbers |
| idempotencyKey | text | Prevents double booking (unique per user) |
| searchRequestId | text | Unused (100% null). **No direct link to the search**, so search→booking has to be joined via `chatSessionId` |
| fareOptionId | text | Selected fare option |
| fareSnapshotJson | jsonb | Fare at time of booking: `totalPrice, components, fareName, fareIdentifier, cabinClass, currency, source, tripInfo, isLcc, adultPrice, miniRule` |
| conditionsJson | jsonb | Supplier booking rules: `passportRequired, dobRequired, gstRequired, international, seatMapAvailable, bookingAmendmentAllowed, …` |
| amountPayable | numeric | Total amount in INR (min ₹2,239, avg ₹12,451, max ₹2,60,314) |
| currency | text | `INR` |
| status | enum `FlightBookingStatus` | In use: `BOOKED` 29, `FAILED` 15, `PENDING_BOOK` 11, `PENDING` 2. (Enum also: TICKETED, PENDING_PAYMENT, CANCELLED) |
| failureReason | text | e.g. Tripjack API 400 errors (9), "User abandoned payment" (4), "airline couldn't complete" (2) |
| contactEmail / contactPhone | text | **PII** |
| gstInfoJson | jsonb | Business GST details (**PII**; 9 rows) |
| bookRequestJson | jsonb | Request sent to Tripjack: `travellers` (**PII**: names/DOB/passport), `deliveryInfo`, `amount`, `bookingId` |
| bookResponseJson | jsonb | Tripjack response: `status, pnr, trips, travellers, totalAmount, bookedAt` |
| createdAt / updatedAt | timestamp | |
| ticketedAt / cancelledAt | timestamp | Not populated yet |
| paymentOrderId / paymentTransactionId | text | Link to `payments.PaymentOrder` / `PaymentTransaction` |
| paymentStatus | text | `CAPTURED` 29, `VOID_PENDING` 6, `VOIDED` 5, `CREATED` 4, `CAPTURED_AWAITING_PROVIDER` 2, null 11 |
| confirmationEmailSentAt | timestamp | When the confirmation email went out |
| chatSessionId | text | Chat the booking came from (no FK; 16% null) |
| visibleToUserIds | text[] | Users who can see this booking (group trips) |
| walletAmount | int | Amount paid from the Bookkr wallet (avg ₹7,803) *(verify units)* |
| details | jsonb | Normalised booking details: `journeys, travellers, fare, rules, capturedAt, version` |
| pricingRules | jsonb | Applied pricing: `platformFee, rulesVersion` (2 rows) |

**Status × payment (important):**

| status | paymentStatus | count | ₹ amount |
|---|---|---:|---:|
| BOOKED | CAPTURED | 29 | 1,79,654 |
| PENDING_BOOK | — | 11 | 84,708 |
| FAILED | VOID_PENDING | 6 | 2,81,242 |
| FAILED | VOIDED | 5 | 1,10,114 |
| FAILED | CREATED | 4 | 38,179 |
| PENDING | CAPTURED_AWAITING_PROVIDER | 2 | 15,805 |

⚠️ **6 failed bookings worth ₹2.8 lakh are still `VOID_PENDING`, and 2 are captured but still waiting on the provider.** These customers may have been charged without getting a ticket. An ops alert for this is worth building first.

### 2.8 `HotelSearchRequest`: each hotel search (started 2026-10-22)

| Column | Type | Description |
|---|---|---|
| id | text PK | |
| chatSessionId | text FK (set null) | |
| userId | text FK→User | |
| destinationName | text | Free-text place: "Mumbai, Maharashtra, India" 14, "Indiranagar, Bengaluru…" 13, "South Goa" 8, Udaipur, Jaipur, Jodhpur… The same city appears in several spellings, so it needs normalising |
| destinationLat / destinationLng | float | Geocoded destination (31% null) |
| checkInDate / checkOutDate | timestamp | Stay dates (median 2 nights, p90 4) |
| adults / children | int | Guests (avg 3.2 adults, so mostly group trips) |
| currency | text | `INR` |
| filters | jsonb | Optional `amenities, maxPrice, propertyTypes` (rarely used) |
| searchHash | text | De-duplication hash |
| createdAt / updatedAt | timestamp | |

### 2.9 `HotelSearchResult`: supplier response for a hotel search

| Column | Type | Description |
|---|---|---|
| id | text PK | |
| searchRequestId | text FK→HotelSearchRequest | 1:1 in practice |
| provider | text | `serp_google_hotels+tripjack` |
| rawResponse | jsonb | `{metadata, total, hotels[], nextPageToken}`. Each hotel: `name, providerPropertyId, hotelClass, propertyType, totalPrice, perNightPrice, currency, mealBasis, cancellation, address, latitude, longitude, amenitiesRaw, images, rating, reviewCount, …`. Median 86 hotels per search (0–353) |
| retainedUntil | timestamp | Data-retention expiry (~30 days). **Old results get purged**, so reports must not rely on history older than 30 days here |
| createdAt | timestamp | |

---

### 2.10 Copied for ops-board parity (2026-11-25, TRACKER R15)

What `sync.py` copies, and what it deliberately leaves behind. Free-text reasons are regex-masked (`masking.py`).

| Analytics table | Source | Columns copied | Never copied |
|---|---|---|---|
| `raw_hotel_bookings` | `HotelBookingState` | id, user/chat ids, status, amount (paise), currency, wallet amount, refund amount, cancellation charge, hold flag, failure reason (masked), payment order id, stay city, created/updated/cancelled/book-requested times | travellers, contact, GSTIN, voucher URL, Razorpay ids on the booking, details JSON |
| `raw_payment_orders` | `payments.PaymentOrder` | id, Razorpay order id, amount, currency, status, reference type/id (booking), user id, created/updated/authorized/captured/voided times | receipt, notes |
| `raw_payment_txns` | `payments.PaymentTransaction` | id, order id, Razorpay payment id, status, method, amount, currency, fee, tax, error code/description (masked)/source, times | card last 4, card network, bank, VPA, wallet |
| `raw_refunds` | `payments.Refund` | id, transaction/order id, amount, status, reason (masked), initiated by, speed, initiated/processed/failed times, failure reason (masked) | Razorpay refund id, notes |
| `raw_payment_webhooks` | `payments.WebhookEvent` | event, Razorpay order id, payment id, status, amount (lifted out of the payload in the source query), received time | the payload itself (payer email, phone), signature |
| `raw_wallets` | `Wallet` | id, user id, balances (any / flight / hotel, paise), currency, times | |
| `raw_wallet_entries` | `WalletEntry` | id, wallet id, kind, status, amount, reference type/id, bucket, restricted amount, times | admin user id, note, idempotency key |
| `raw_support_tickets` | `SupportTicket` | id, number, user id, priority, status, source, unread flag, last message / closed / created / updated times | subject, messages, closing admin |
| `raw_hotel_sync_runs` | `HotelCatalogueSyncRun` | id, trigger, status, start/finish, hotels, cities, content missing, error (first 400 chars, masked) | admin who started it, countries |
| `raw_user_days` | `AnalyticsEvent` (aggregated in the source query) | user id, IST day, platform (web/app/other), events a person caused, "engaged" events, first/last event time | event properties, IP, location |
| `raw_checkout_events` | `AnalyticsEvent` (booking_step_viewed/completed, payment_checkout_opened, booking_confirmed) | id, event, user id, chat id, booking attempt id, step, product, skipped, booking id/status, platform, time | other properties |
| `raw_server_errors` | `AnalyticsEvent` (server_error, request_rejected) | id, event, user id, platform, method, status, error type, path (ids replaced by `:id`, query string dropped), time | messages, request bodies |
| `raw_visits` | `SourceVisit` | id, visitor id, source, referrer host, landing path (no query string), time | raw source text |
| `raw_attribution` | `AnalyticsEvent` (visit_attributed) | id, user id, platform, UTM source/medium/campaign (masked), source, referrer host, landing path (no query string), time | other properties |

`raw_flight_bookings` also gains `payment_order_id` and `payment_transaction_id` (ids only), to tie bookings to Razorpay.
Derived: `bookings_all` (flights + hotels in one table: confirmed / cancelled / failed / abandoned, value incl. wallet,
wallet share, airline, route, city).

## 3. Funnel snapshot (all-time, by chat session)

| Stage | Sessions | % of sessions |
|---|---:|---:|
| Chat sessions created | 326 | 100% |
| User sent ≥1 message | 313 | 96% |
| Flight search run | 158 | 48% |
| Hotel search run | 70 | 21% |
| Flight booking attempted | 34 | 10% |
| Flight booking succeeded (`BOOKED`) | 25 | 7.7% |

Distinct users: 43 chatting → 40 searched flights → 7 searched hotels → 5 attempted bookings.

---

## 4. Things to settle before automated reporting
1. **Test vs real users:** with 43 users and a launch-like spike, internal and test accounts probably dominate. We need a rule, e.g. an email-domain list or a flag on `User`.
2. **Timezone:** confirm timestamps are UTC, and report in IST.
3. **Booking "success" definition:** is `BOOKED` + `CAPTURED` enough, or must the booking be `TICKETED`? (`ticketedAt` is never set today.)
4. **PII:** `Message.content`, `FlightBooking.contactEmail/contactPhone/gstInfoJson/bookRequestJson` must be **excluded or masked** in the agent's read-only views.
5. **Hotel bookings:** not covered by these 9 tables. They appear to be in `HotelBookingState` / `Booking`, which need adding for hotel revenue.
6. **Flight fare details:** these are in `FlightOption`. Add it if reports should cover the prices users saw against the prices they booked.
