<!-- Generated 2026-11-25 from the UAT code snapshot of 19 Nov 2026 (bookkr-combined-uat.zip). Regenerate when Bookkr's turn pipeline changes. -->
## overview

"Rule" means fixed code; "LLM" means a model call logged to `LlmCallLog` under the task shown. A chat turn is `POST /v2/chat/message` → `ChatController.handleUnifiedMessageInternal` (`bookkrapp/src/modules/chat/ChatController.ts`). In order:

1. Save the user message. In group chats an engagement gate (rule) decides if Bookkr answers at all; slash and planning commands are answered here.
2. Deterministic "doors" that skip all LLMs: traveller-count answer, "Switch to / Go back to <dates>", plan "Find hotels" / "Find flights" buttons, plan switch phrases (rules).
3. Sticky hotel gate (rule): if a hotel question is open, fresh (15 min) and was the last reply, the turn is offered to hotels. The hotel extractor (LLM, `hotel_intent_extraction`) returns `turnScope` stay/other; "other" hands the turn back.
4. `UnifiedTravelOrchestrator.process` (`bookkrapp/src/modules/intent/`):
   - extract (LLM `unified_intent_extraction`: LoRA first, OpenAI fallback);
   - merge with earlier turns, airport choice, location seed, group-size seed, relative-date and past-date rules (rules);
   - multi-route check (LLM `multi_route_check`, only on some flight turns);
   - same-city guard, airport asks, trip-length conflict ask (rules);
   - `ActionDecider` picks ask / hotel / general / flight / plan / DAG (rule);
   - confirmation-card gate (rule).
5. Dispatch:
   - Flight: a second extraction (LLM `intent_extraction_thread`) → `intent_ready`. The client then calls `POST /v2/chat/search` → providers → `PreferenceEngine` (rule) → reply (LLM `generate_response`, fixed text when nothing is found).
   - Plans: turn rules (rule), then the plan router or planner (LLM `multi_city_intent`); date options and leg flights (rules plus providers).
   - Hotels: `HotelOrchestrator`.
   - General: unsupported-feature check (rule), then `web_search_category` / `web_search_grounded` (LLM), else `generate_response`.
6. Reply chips (LLM `nudge_recommendation`) are attached to the HTTP response. The message is saved and published (SSE to web, Ably to phone).
7. Background: `soft_signals` (LLM) after a confirmation card.

`unified_chat_route` does not run on the default path (see its section).

## flows

`UnifiedFlowState.currentFlow` is a Prisma enum with three values: `single_flight`, `multi_city`, `unresolved` (`bookkrapp/prisma/schema.prisma`). New chats start `unresolved`. There is no hotel value: hotel turns never change it. The judge's `flow=` fact is the session's last committed value, not the flow of each turn.

What decides a turn's flow on the default path (`UNIFIED_INTENT_ENABLED` defaults to `true`):

1. The extractor's `actionHint` (LLM). Values: `search_flight`, `search_hotel`, `plan_itinerary`, `undecided`, `travel_general`, `out_of_domain`, `confirm_and_execute`, `request_summary`, `reject_confirmation`.
2. `mergeActionHint` (rule, `intent/TravelIntent.ts`): when this turn's hint is `undecided` or transient (confirm/summary/reject), the previous turn's hint is revived. A stale hotel or plan hint can therefore steer a vague turn.
3. `ActionDecider.decide` (rule). Mid-plan phases (`itinerary_generated`, `dates_generated`, `flights_shown`) send plan, undecided and plan-referencing flight turns to the multi-city DAG. If the hint is `undecided` and both a flight and a plan are complete, the decider picks plans when trip length, companions or interests are set. Flexible destination with no destination goes to general assistance.
4. Plans on screen: `decideTurn` in `multi-city/turnRules.ts` (rule) decides what the turn does: a flights request that also changes dates, origin, travellers or destination regenerates plans (rule 5).
5. `currentFlow` is written by code after the dispatch (`setCurrentFlow`, `updateFlightState(..., 'single_flight')`, `updateMultiCityState(..., 'multi_city')`), never by an LLM directly.

So a wrong flow is usually the extractor's hint or a fixed rule (revival, decider, turn rules), not a router LLM.

## unified_intent_extraction

**What it decides.** Every engaged turn's `TravelIntent`: places, dates, party, trip type, cabin, budget, preferences and `actionHint`. It also writes `assistantMessage`, the text used for asks with no specific missing slot, for "no" to a card and for "what do you have so far". Served by a W&B LoRA (model id starts `wandb-artifact:`) when enabled, else OpenAI (`GPT_MODEL_UNIFIED_INTENT_EXTRACTION`).

**In the data.** Its output is the "EXTRACTED INTENT (travel_intent …)" line. A LoRA failure writes a failed `unified_intent_extraction` row with the LoRA model, then a success row with the OpenAI model; the user saw nothing. If both fail the user gets a `shared_failure` message "Temporary issue on our side. Please retry." A schema failure returns "We couldn't understand that request. Please try rephrasing." and saves no assistant message.

**Tell apart.** Blame it only when the travel_intent line itself is wrong. If that line is right and the search differs, a rule owns it. The second, flight-only extraction is a different call (`intent_extraction_thread`; see `flight_state_carryover`) even though `TASK_OWNER` maps it here. A hint that looks "revived" is the merge rule, not the model.

Files: `bookkrapp/src/modules/llm/LLMService.ts` (`extractUnifiedTravelIntent`), `bookkrapp/src/modules/intent/unifiedIntentExtractionPrompt.ts`, `intent/unifiedIntentExtractionSchema.ts`, `llm/unifiedIntentCanonicalizer.ts`, `intent/applyExtraction.ts`.

## unified_chat_route

**What it decides.** It classifies a message as `single_flight`, `multi_city`, `travel_general`, `out_of_domain` or a clarification. It runs only inside `UnifiedMessageRouterService.resolveFlow`, which runs only on the kill-switch path, when `UNIFIED_INTENT_ENABLED=false`. Even there, fixed regex rules run first (`flow-router/routingPolicy.ts`): explicit switch phrases, conflicting signals, active-flow continuation, strong plan or flight signals. The LLM is asked only when none match, and only answers at confidence 0.7 or more. If the call fails, a keyword scorer decides.

**In the data.** Rows with task `unified_chat_route` mean the kill switch was on. Without such rows, this owner did not act. Its fixed clarifications are "Are you booking a single flight, or planning a multi-city trip (cities + round-trip flights)?" and "Do you want me to update the current trip plan options…".

**Tell apart.** On the default path a wrong flow is owned by `unified_intent_extraction` (wrong `actionHint`), `action_decider` (merge revival, decider branches), `plan_turn_rules` (plans on screen) or `hotel_intent_extraction` (sticky hotel gate). Do not use this owner without evidence that the kill switch was on.

Files: `bookkrapp/src/modules/flow-router/UnifiedMessageRouterService.ts`, `flow-router/routingPolicy.ts`, `chat/ChatController.ts` (the `resolveFlow` call after "End unified intent pipeline").

## hotel_intent_extraction

**What it decides.** `HotelIntentExtractor` reads hotel turns: city, area or landmark, dates, guests, rooms, budget, amenities, stars and named property. It also returns `turnScope` (`stay` or `other`). It is called through `generateResponse(prompt, 'hotel_intent_extraction')`, so its model is the `generate_response` model. `HotelOrchestrator.offer` (sticky gate) uses `turnScope`. `process` (Hotels tab, or a turn the unified extractor already sent to hotels) never declines.

**In the data.** Output is hotel cards (`hotel_card`, with inner `kind` such as clarification, error or results) and "HOTEL SEARCH …" lines. Invalid JSON or a schema failure does not fail the call: the extractor silently keeps the previous hotel intent with `turnScope: 'stay'`, and the LlmCallLog row says success. So a "nothing changed, user repeats" hotel turn can be this owner with no failure row.

**Tell apart.** A non-hotel question answered as a hotel turn while a hotel card was open: this owner (`turnScope` said stay). A hotel turn reached with no hotel card open: the unified extractor's `search_hotel` hint. Hotels answering "Hotel search is temporarily unavailable…": `hotel_provider` or `backend`.

Files: `bookkrapp/src/modules/hotel/HotelIntentExtractor.ts`, `hotel/HotelOrchestrator.ts`, `chat/ChatController.ts` (sticky gate, `unified_hotel.sticky_*`).

## multi_city_intent

**What it decides.** One task name covers every LLM call inside the plan DAG (`LLMService.extractMultiCityIntent`):
- the plan router model (turn rule 9, which plan or stop the words mean, within an allowed scope);
- itinerary generation and edits;
- plan Q&A (`answer_itinerary_question`);
- feasibility repair;
- airport resolution for plan cities;
- airport-transfer explanations.

**In the data.** Cards `itinerary_options`, `itinerary_dates`, and `router_clarification` replies. A row with `success=false` and error `empty_response` means the model returned nothing. A failed planner gives "I could not build trip plan options just now — please try again." (response meta `autoSearchFailed`). A shared failure gives "Trip plan search is temporarily unavailable. Please retry." A planner question (ValidationError) is shown as written.

**Tell apart.**
- Which action a plan turn takes is the turn rules' decision first. The router model only acts at rule 9, and its own words are never shown.
- A regenerate after a dates/origin/travellers/destination change is `plan_turn_rules`.
- An unrealistic or repetitive plan is this owner (suggestion).
- Bad date options are `date_options`. Wrong leg airports can be `airport_choice`.
- LlmCallLog cannot tell the sub-calls apart.

Files: `bookkrapp/src/modules/multi-city/MultiCityOrchestrator.ts`, `multi-city/conversationRouterPrompt.ts`, `multi-city/searchItineraryPrompt.ts`, `multi-city/multiCityPrompt.ts`.

## multi_route_check

**What it decides.** Whether a flight turn asks for more than one route at once ("Kanpur or Lucknow to Udaipur", open-jaw, separate flights). It runs only when `shouldCheckMultiRoute` (rule) passes:
- the raw hint is `search_flight`;
- the turn is not about the plan on screen;
- it is not an answer to the airport chooser;
- the turn has its own route or a list word.

It is always OpenAI and has a 3-second timeout. A timeout or error fails open: the turn searches as before.

**In the data.** A positive answer gives the fixed reply `MULTI_ROUTE_FLIGHT_REPLY` ("Searching flights for more than one route in one go isn't supported yet…") and analytics `unsupported_feature_requested` with feature `multi_route_flights`. With no plan open, the turn's cities are cleared from the stored intent.

**Tell apart.** That reply on a single-route request: this owner (wrong flow). A real multi-route request that got it: `product`. A multi-route request that searched one route: this owner missed it, or the gate never called it (`other_rule`), or it failed open (`llm_provider`). "Do you do multi-city flights?" is answered by a regex, not this LLM.

Files: `bookkrapp/src/modules/intent/multiRouteFlightCheck.ts`, `llm/LLMService.ts` (`classifyMultiRouteFlight`), `intent/UnifiedTravelOrchestrator.ts` (Step 3a-6).

## soft_signals

**What it decides.** Implied flight preferences from the latest message: time windows, layovers, price sensitivity band, airlines, cabin willingness. It runs in the background from `TravelOrchestrator.initializeUserChatPreference`, started after a confirmation card. The signals feed flight ranking, the hotel price weight (`PRICE_SENSITIVITY_BAND`) and the chips prompt.

**In the data.** Nothing user-visible. On failure or unparseable output it returns empty signals. A failed row has error `invalid_json` or another reason; it is a quiet task (`QUIET_TASKS`).

**Tell apart.** Results ignoring a preference the user stated explicitly ("non-stop", "IndiGo only", "under ₹5,000") are first the extractor (was it in the travel_intent line?), then `search_builder` (non-stop is a search param) or `ranking`. Use this owner only for an implied preference that should have reordered results and did not, and only when the explicit fields were empty. It is hard to evidence from transcripts. Prefer `unknown` over guessing it.

Files: `bookkrapp/src/modules/llm/LLMService.ts` (`extractSoftSignals`), `orchestrator/TravelOrchestrator.ts`, `preference/PreferenceAccumulator.ts`.

## generate_response

**What it decides.** Free prose. `LLMService.generateResponse` logs task `generate_response` unless given another name. Callers:
- `ResponseComposer`: flight results summary, general travel answers without web results, out-of-domain redirect, unsupported-service turns;
- plan and command services, group mediation, destination dossier.

`visible_thread` (`generateChatThreadResponse`) is the thread reply composer.

**In the data.** Any assistant text that is not a card's fixed copy. On failure most callers fall back to fixed text. Flight results: "I've found N flights from X to Y on D. Results are shown in the right panel." (a Hinglish variant exists). The user rarely sees an error.

**Tell apart.**
- Zero flight results use fixed text ("I couldn't find any flights … No fares came back for this search…"), not this LLM.
- Follow-up questions with missing slots are `action_decider` templates.
- Asks without slots come from the extractor.
- Cab/train/bus "coming soon" and multi-route replies are fixed.
- A factual answer with citations is `web_search`.
- Claims about ranking ("I've moved IndiGo to the bottom") are written from the ranking context; a false claim is this owner only if the ranking context was absent.

Files: `bookkrapp/src/modules/orchestrator/ResponseComposer.ts`, `llm/LLMService.ts`, `flow-router/GeneralTravelAssistantService.ts`.

## web_search

**What it decides.** For general-assistance turns, `web_search_category` (LLM) decides if the web is needed and writes a query. The query is then checked by a policy (rule), and the confidence must reach `WEB_SEARCH_CONFIDENCE_THRESHOLD`. The answer comes from:
- the OpenAI web-search tool (`web_search_grounded`); or
- Tavily snippets plus a `ResponseComposer` grounded prompt (logged as `generate_response`).

Budget discovery ("₹50k, where should I go?") runs first on the Tavily path. `suggestion_research` / `suggestion_extract` belong to the suggested-trips feed, not chat turns.

**In the data.** Grounded replies carry citations and a `groundingCategory` in message metadata. Any failure (classifier error, policy block, empty results) silently falls back to an ungrounded `generate_response` answer with no citations.

**Tell apart.**
- A wrong fact with citations is this owner.
- A wrong fact without citations is `generate_response`.
- An answer to a request Bookkr cannot fulfil is `product`, unless the fixed unsupported-feature regex should have caught it (`other_rule`).
- A travel question routed to a search (or the reverse) is an extractor hint problem.

Files: `bookkrapp/src/modules/flow-router/GeneralTravelAssistantService.ts`, `llm/LLMService.ts` (`classifyWebSearchCategory`, `composeOpenAIGroundedAnswer`), `bookkrapp/src/modules/services/` (WebSearchService, unsure of exact file).

## nudge_recommendation

**What it decides.** Up to two short reply chips under a reply. The LLM writes candidates and a fixed filter (`isAllowedNudge`, `llm/nudgeRules.ts`) drops ones with money or past dates. No chips are written under an intent confirmation card. The traveller-count question gets fixed chips "2, 3, 4, 5+". Plans on the traveller's exact dates drop date chips (rule).

**In the data.** Chips ride only on the HTTP response (`recommendedMessages`). They are not saved on the `Message` row, so transcripts do not show them. The only evidence is a user message that exactly matches a chip ("Direct flights only") or the user complaining about it. Failure returns no chips (a failed `nudge_recommendation` row, nothing visible).

**Tell apart.** If a tapped chip leads to a bad outcome, judge the turn the chip text started like any typed message: the owner is whoever mishandled that text. This owner is only for a chip that was itself misleading or offered something Bookkr cannot do. Be conservative: chips are mostly invisible to you.

Files: `bookkrapp/src/modules/chat/NudgeRecommendationService.ts`, `chat/nudgeReplyContext.ts`, `llm/nudgeRules.ts`, `chat/ChatController.ts` (`attachRecommendations`).

## hotel_highlights

**What it decides.** Short AI-written highlights on the new hotel detail view. It is paid and behind a flag, and runs only when a hotel detail is opened (`HotelChatController`, `generateResponse(prompt, 'hotel_highlights')`, model `GPT_MODEL_HOTEL_HIGHLIGHTS`).

**In the data.** Shown inside the hotel detail UI, not as a chat message. Transcripts will almost never show it; evidence would be a user quoting or disputing a highlight.

**Tell apart.** Wrong hotel facts in the chat reply are `generate_response`; wrong rows, prices or amenities on the card are `hotel_provider` data; order is `ranking`. Rarely the right owner.

Files: `bookkrapp/src/modules/chat/HotelChatController.ts`, `llm/LLMService.ts`.

## date_policy

**What it decides.** Fixed date rules after extraction (`UnifiedTravelOrchestrator.process` Steps 3a-4/3a-5, and also in `HotelOrchestrator`):
- Relative single-day phrases ("next Friday") are computed in code, overriding a disagreeing model date.
- Past dates: a date with no year moves to its next occurrence. A past year the user typed clears the dates and asks again. Ambiguous `03/05/2027` is asked.
- Exact window needs two signals, the extractor's two ends and a deterministic read; hedges ("flexible", "ke aas paas") make it flexible (`exactWindowRule.ts`, `tripWindowClassifier.ts`).
- Mixed round trip: one fixed leg plus one flexible leg, by the dates (`mixedTripDates*.ts`). Flight turns only, never while a plan is in play.
- A stated length gives a flexible round trip its return window (`tripLengthReturnWindow.ts`).
- Trip length vs dates conflict asks once (`statedTripWindow.ts`).

**In the data.** A "travel dates" ask after a past date; flexible-strip cards (`flight_date_strip`, `flight_flex_strip`) where the user meant exact dates.

**Tell apart.** The travel_intent line holds dates after these rules ran. A shifted month or year, or exact/flexible, that matches a rule is this owner. A plainly misread day is the extractor.

Files: `bookkrapp/src/modules/intent/futureDatePolicy.ts`, `intent/relativeDateResolver.ts`, `intent/exactWindowRule.ts`, `intent/mixedTripDates.ts`, `intent/tripLengthReturnWindow.ts`.

## action_decider

**What it decides.**
- **Branch.** `ActionDecider.decide` picks the branch from the merged intent: general, flight, hotel, plan-for-hotel, discovery, itinerary, DAG, or the undecided inference.
- **Missing slots.** `TravelIntentValidator` lists them; the question words are a template (`followUpQuestions.ts`).
- **Confirmation gate** (Step 4c). It shows "Here is what I have so far. Would you like to proceed?" the first time an intent is complete. It searches directly when the route and party match a search already confirmed in this chat and the turn asks for a search.
- **Repeat suppression** and **hint revival** (`mergeActionHint`) sit beside it.

**In the data.** `intent_confirmation` cards; template asks such as "When would you like to fly from Bengaluru to Delhi?"; the undecided ask "Are you looking to book a specific flight, or would you like help planning a full trip itinerary?".

**Tell apart.**
- An ask for a field already given: the extractor if it is missing from the travel_intent line; this owner if present but still asked.
- An undecided turn turning into plans because a duration was mentioned is this rule.
- Asks with no slots are worded by the extractor.

Files: `bookkrapp/src/modules/intent/ActionDecider.ts`, `intent/TravelIntentValidator.ts`, `intent/followUpQuestions.ts`, `intent/confirmedSearch.ts`, `intent/TravelIntent.ts`.

## airport_choice

**What it decides.**
- **Single flights.**
  - The destination picklist: `maybeBuildAirportAsk` lists the city's own airports, biggest hub first, then gateways, 3–5 options, from `AirportLookupService.findCityAirportOptions`. A single candidate is chosen silently (`sole_candidate`).
  - The departure-airport ask for multi-airport origins (`departureAirportPolicy.ts`).
  - Reading the user's pick (`applyPendingDepartureAirportChoice`, `applyPendingAirportChoice`), and the repeat guard (`isSameAirportAsk`).
  - Location seed: browser city fills the origin when none was given.
- **Plans.** `multi-city/airportChoice.ts` switches a leg to another airport set only if it saves at least ₹3,000 per person and is at least a fifth cheaper.
- **"How do I reach a place with no airport"** uses an LLM (`airport_guidance`, not in the taxonomy).

**In the data.** Asks with bullets like "• **London Heathrow Airport** (LHR) · ~23 km away"; missing slots `origin.iata` / `destination.iata`; a search line with an unexpected airport.

**Tell apart.** Right city in the travel_intent line but another airport in the search: this owner. An origin from the device's city that the user never named: the location seed (this owner).

Files: `bookkrapp/src/modules/intent/UnifiedTravelOrchestrator.ts` (Step 3c), `intent/departureAirportPolicy.ts`, `location/` (AirportLookupService), `multi-city/airportChoice.ts`, `multi-city/legAirports.ts`.

## search_builder

**What it decides.** It turns the stored flight intent into provider parameters (`FlightSearchParamsBuilder`, built in `TravelOrchestrator.buildSearchParams` and the round-trip and leg builders):
- origin, destination, dates, cabin (default ECONOMY), adults (default 1), children, infants, currency;
- `nonStop` and `passengerFareType` (Student / Senior).

The combination (`ONE_WAY|EXACT_DATE`, `ROUND_TRIP|DATE_RANGE`…) routes exact vs flexible search (`routeFlightSearch`). With Student/Senior fares only Tripjack is searched.

**In the data.** The "FLIGHT SEARCH: X → Y on D, N adult(s), CABIN" line versus the intent lines.

**Tell apart.**
- Intent lines right, search line wrong in a way no date or airport rule explains (cabin lost, party lost, return dropped): this owner.
- A wrong airport: `airport_choice`. A wrong date: `date_policy`.
- The flight_search intent line already wrong: `flight_state_carryover`.
- Flexible-date searches price 1 adult on purpose (strip and calendar); that is not this owner's bug.

Files: `bookkrapp/src/modules/builders/FlightSearchParamsBuilder.ts`, `orchestrator/TravelOrchestrator.ts`, `orchestrator/FlightIntentValidation.ts`, `orchestrator/flexStripSearch.ts`.

## ranking

**What it decides.** Order, not filtering.
- **Flights.** `PreferenceEngine` scores price, airline, departure time, non-stop bonus, duration and airport. Weights come from chat signals, then user defaults, then saved preferences, then defaults. Avoided airlines and airports drop to the bottom (`avoidTier`). A budget is a soft price cliff, never a filter.
- **Hotels.** `HotelPreferenceEngine` uses the default profile `location_dominant` (location 0.60). An explicit budget or amenities switch learned signals off. Typed hotel names get a fixed score lift. `rankChips` marks the cheapest and top-rated rows.

**In the data.** Result cards (`flight_results`, `hotel_card`); "USER USED … sort/filter" activity lines after results suggest the order did not fit.

**Tell apart.**
- "Cheapest" asked, cheapest not first: this owner, if the request reached the intent.
- "Non-stop only" showing stops: the search parameter (`search_builder`), or a provider counting stops differently, not ranking.
- Results over budget are expected (soft), so only the order matters.
- The right results with poor order is suggestion/`ranking`; wrong results is intent or a rule.

Files: `bookkrapp/src/modules/preference/PreferenceEngine.ts`, `preference/scoring-config.ts`, `hotel/HotelPreferenceEngine.ts`, `hotel/ranking/HotelRankingProfile.ts`, `hotel/rankChips.ts`, `orchestrator/TravelOrchestrator.ts` (`rankResults`).

## date_options

**What it decides.** For plans with a flexible window, which date pairs to offer (`itinerary_dates` card). It uses month resolution, leave and weekend calendars and calendar prices (`dateOptionAlgorithm.ts`, `dateOptionSelectionV2.ts`, `dateOptionPresentation.ts`). A plan on exact dates shows no dates card. For single flights the flexible strip's auto-pick is the cheapest day, or the cheapest pair within ±2 days of the stated length (`flexStripSearch.ts`). Only the newest date-options card stays visible.

**In the data.** `itinerary_dates` cards; "Date options are temporarily unavailable. Please retry." (shared failure); strip cards. Calendar provider failure: "I could not reach the flight calendar service just now — please try again in a moment." (`calendarFailed`).

**Tell apart.**
- Options that ignore the stated window or length: this owner if the window is correct in the intent; `date_policy` if it was marked flexible when the user gave exact dates.
- No options because of a calendar failure is `flight_provider`.
- Regenerating plans instead of offering dates is `plan_turn_rules`.

Files: `bookkrapp/src/modules/multi-city/dateOptionAlgorithm.ts`, `multi-city/dateOptionSelectionV2.ts`, `multi-city/dateOptionPresentation.ts`, `orchestrator/flexStripSearch.ts`.

## plan_turn_rules

**What it decides.** With plans on screen, `decideTurn` decides the turn in this order:
1. answer to our "which plan?";
2. yes/no with nothing pending gets the next-step question;
3. edit of one plan;
4. flights with no core change get the plan's flights;
5. a core change (dates, origin, travellers, destination) regenerates all plans, even if one plan is named, even for a flights request;
6. a question;
7. a trip-wide preference regenerates;
8. a plan number the card lacks gets an out-of-range ask;
9. otherwise the router model (LLM) decides, with a restricted action list.

Also: one plan on screen is "the plan"; a bare plan reference never modifies; LHR→LGW counts as an origin change; the ChatController plan doors act here.

**In the data.** A new `itinerary_options` card where the user asked for flights or an edit; "which plan" questions; the next-step question.

**Tell apart.** A wrong plan action is this owner when rules 1–8 fired; only rule 9 is `multi_city_intent`. With no plans on screen, a flight turn going to plans is the extractor hint or `action_decider`.

Files: `bookkrapp/src/modules/multi-city/turnRules.ts`, `multi-city/turnClassification.ts`, `multi-city/intentDelta.ts`, `multi-city/pendingPlanQuestion.ts`, `chat/ChatController.ts`.

## flight_state_carryover

**What it decides.** How the flight search intent is rebuilt from earlier state. Three stages:
1. The unified merge maps into `flightState.entities` (`TravelIntentMapper.toFlightEntities`, rule).
2. `TravelOrchestrator.extractIntentFromHistory` runs a second LLM extraction (task `intent_extraction_thread`) on the same message, with those entities as the "previous intent". If there are no entities it uses Redis `last_extracted_intent:<chat>` or the DB row. It merges the result and saves the "EXTRACTED INTENT (flight_search …)" row.
3. At search time `loadFinalIntent` reads Redis `intent:<chat>`, else the DB's `latestExtractedIntentId`.

Mixed and derived return legs are restored by rule (`withMixedTripLegs`, `withDerivedReturnLegs`).

**In the data.** A travel_intent line and a flight_search line in the same turn that disagree. A search built from an older trip's values.

**Tell apart.** If the travel_intent line is right and the flight_search line is wrong, the cause is the mapper, the cache (both rules), or the second LLM re-read. From the data you cannot tell which, and `TASK_OWNER` maps `intent_extraction_thread` to `unified_intent_extraction` (unsure which is right). Use this owner unless the flight_search line shows a fresh misreading of this turn's words. A failed `intent_extraction_thread` gives "Temporary issue on our side. Please retry."

Files: `bookkrapp/src/modules/orchestrator/TravelOrchestrator.ts` (`extractIntentFromHistory`, `loadFinalIntent`), `intent/TravelIntentMapper.ts`, `orchestrator/IntentContextService.ts`, `orchestrator/intentRedisKey.ts`.

## other_rule

**What it decides.** Fixed rules with no owner of their own. Main ones:
- Unsupported-service regex (`flow-router/unsupportedFeatures.ts`): cabs, buses, trains, activities, day-by-day itineraries get a fixed "coming soon" reply before any LLM. It runs only on general-assistance turns. Asking who provides a service locally is treated as a question.
- Sticky hotel gate conditions and escape regexes (`hasStrongFlightSignal` etc.).
- Group engagement gate (`chat/ChatEngagementGate.ts`).
- Same-city guard, multi-route gate (`shouldCheckMultiRoute`), group-size seed (fills adults from chat size on "all of us").
- Event-to-destination fallback (an unlogged web call).
- Hotel confirmation rows; fare category carried on the card.
- Typo normalisation for classifiers (`intent/classificationText.ts`).

**In the data.** Analytics `unsupported_feature_requested` with a feature; fixed replies; group traveller counts that nobody typed.

**Tell apart.** Use this only when a named rule above made the first wrong decision and no specific owner fits. A "coming soon" on a real booking request for an unsupported service is `product`, not this owner.

Files: `bookkrapp/src/modules/flow-router/unsupportedFeatures.ts`, `chat/ChatEngagementGate.ts`, `intent/groupTravelPhrase.ts`, `intent/UnifiedTravelOrchestrator.ts`.

## flight_provider

**What it covers.** In `gateway` mode (the default, `GatewayBackedFlightProvider`), searches fan out to SerpAPI and Tripjack in parallel through the travel-gateway service and are merged. If one provider fails it is logged only; the other's results show. If all fail, the search throws. The calendar search is used for flexible dates and plan date options. Tripjack SME/corporate fares are dropped. Fares are held 15 minutes (`pricesExpireAt`).

**In the data.**
- All providers failed: "FLIGHT SEARCH … → no result stored" plus `shared_failure` "Search service is temporarily unavailable. Try again." (leg: "Flight search is temporarily unavailable for this leg. Please retry.").
- Calendar failure: "I could not reach the flight calendar service just now…".
- Provider success with zero fares: a stored result with 0 options, the fixed "I couldn't find any flights… No fares came back for this search" text, and "FLIGHT RESULTS SHOWN: 0 results".

**Tell apart.** Zero results on a correct search is usually no availability (not friction), unless an immediate retry succeeded. Student/Senior searches use Tripjack only. "No result stored" or a shared failure is this owner. A dead fare at quote time is `payments_booking`.

Files: `bookkrapp/src/modules/services/flight/gateway/GatewayBackedFlightProvider.ts`, `services/flight/gateway/GatewayFlightProvider.ts`, `services/flight/createFlightProvider.ts`, `services/flight/FlightService.ts`.

## hotel_provider

**What it covers.** TripJack hotels (catalogue plus pricing, through the gateway) and SerpAPI Google Hotels (for a named property, and for prices on open), via `CompositeHotelProvider`. Discovery strategies map a city or area to the catalogue. A search can hold about 1,500 hotels; 20 are returned first.

**In the data.**
- "The hotels provider is having a moment. Please try again in a few seconds." (SerpAPI 5xx, card `kind: 'error'`).
- "Sorry — I had trouble searching hotels. Please try again in a moment."
- "Hotel search is temporarily unavailable. Please retry." (shared failure).
- "I couldn't check <hotel> just now — one of our sources didn't respond…".
- "No hotels found near <anchor> for those dates. Try shifting the dates or relaxing filters."
- "HOTEL SEARCH … → 0 hotels".

**Tell apart.** "No hotels found near…" after a normal search is usually availability or a too-narrow anchor. Check the area in the intent: a wrong anchor is the hotel extractor. "Couldn't find '<place>' on the map" is geocoding of the extracted text, so check the spelling first. Error cards are this owner.

Files: `bookkrapp/src/modules/hotel/providers/CompositeHotelProvider.ts`, `hotel/providers/tripjack/TripJackHotelProvider.ts`, `hotel/providers/SerpHotelProvider.ts`, `hotel/HotelService.ts`, `hotel/HotelOrchestrator.ts`.

## payments_booking

**What it covers.** In-house booking of Tripjack-backed rows. The steps run in a fixed order:
1. fare quote;
2. validate (keeps the provider's total);
3. server-priced payment initiation (409 `AMOUNT_MISMATCH` "Price changed, please re-check the fare.", 409 `FARE_CHECK_EXPIRED`);
4. Razorpay through the separate payments service;
5. `/book` only on authorized or captured money.

There are also hotel book and hold flows, the wallet, and cancellations. Partner-only (SerpAPI) rows are handed off to the partner's site; Bookkr never learns the outcome.

**In the data.**
- `payment_result` cards with outcome confirmed / failed / needsAttention.
- `FlightBooking.status` values PENDING_BOOK, BOOKED, TICKETED, PENDING_PAYMENT, PENDING, CANCELLED, FAILED. `failureReason` holds curated copy keyed by Tripjack errCode:
  - 1000: fare gone;
  - 808 / 2024: expired;
  - 1015: price changed;
  - 1091: older search;
  - 1010: duplicate names;
  - 1163 / 1206 / 1065: PAN or passport data.
- Client events `payment_failed`, `payment_dismissed`, `booking_price_changed`, `booking_fare_expired`.

**Tell apart.** errCode 1091 can come from mixing legs of different searches (refine or calendar-pick), a code issue. A PAN or passport refusal is missing traveller data. "Your bank is still processing this payment" is a parked UPI payment, not a failure.

Files: `bookkrapp/src/modules/booking/BookingPaymentService.ts`, `booking/providerErrorCopy.ts`, `chat/bookValidateErrors.ts`, `hotel/booking/HotelBookingService.ts`.

## llm_provider

**What it covers.** The model services: OpenAI (most tasks), W&B serverless LoRA (intent extraction), or the Hugging Face router if configured. Every call is logged by `persistLLMCall` with `task`, `model`, `latencyMs` and `success`. On failure `error` holds only a reason code: `timeout`, `rate_limited`, `provider_authentication`, `provider_unavailable`, `invalid_json`, `invalid_schema`, `empty_response`, `policy_rejected` or `unknown`.

**What the user sees by task.**
- Silent fallback: LoRA → OpenAI, `soft_signals`, `nudge_recommendation`, `multi_route_check` (fails open), `web_search_*` (ungrounded answer), most `generate_response` callers (fixed text).
- Visible failure: `unified_intent_extraction` and `intent_extraction_thread` ("Temporary issue on our side. Please retry."), `multi_city_intent` planner ("I could not build trip plan options just now").

**Tell apart.** It is technical only if the user saw the effect. A failed LoRA row followed by a successful OpenAI row in the same second is a recovered call, not friction. A success row with a bad answer is the task's owner, not this one. Hotel extractor JSON errors are logged as success.

Files: `bookkrapp/src/modules/llm/LLMService.ts`, `llm/LLMObservability.ts`, `llm/llmTasks.ts`.

## realtime_client

**What it covers.** Delivery of saved messages and progress to clients:
- SSE (`/v2/chat/:id/events`, progress at `/v2/chat/progress/:token/events`) to web; Ably to the phone (`RealtimeService`, `ConversationEventBus`). Ably forwarding is best-effort; a failure is only logged.
- The flight search is client-fired: the chat turn returns `intent_ready`, then the app calls `POST /v2/chat/search`.
- Older mobile builds draw unknown cards as a text bubble plus "This card kind is not drawn inline yet."
- Outdated builds can be blocked with HTTP 426 "Please update Bookkr from the App Store or Google Play to continue."
- Turn stop: a sender can stop a turn, which soft-deletes it (`deletedAt`, never read again).

**In the data.**
- An `intent_ready` turn with no "FLIGHT SEARCH" line after it: the app never fired the search, or the user left.
- Group members on old builds seeing strip cards as stubs.
- Messages missing from history (stopped turns).

**Tell apart.** A missing search after `intent_ready` is this owner. A search that ran and failed is `flight_provider` or `backend`. A stopped turn is the user's own action.

Files: `bookkrapp/src/modules/realtime/RealtimeService.ts`, `chat/ConversationEventBus.ts`, `chat/ChatProgressEventBus.ts`, `chat/turn-stop/`, `bookkrapp/src/middleware/blockOutdatedMobile.ts`, `bookkrapp-mobile/`, `bookkrapp-ui/`.

## backend

**What it covers.** Unhandled errors in the turn or search handlers: database, Redis, lock contention, validation of internal state.

**In the data.** `shared_failure` messages:
- "Temporary issue on our side. Please retry." (chat turn);
- "Search service is temporarily unavailable. Try again." (flight search, rerun, calendar);
- "Trip plan search is temporarily unavailable. Please retry.";
- "Date options are temporarily unavailable. Please retry.".

Also `GENERIC_SERVER_MESSAGE` "Something went wrong on our side. Please try again." and backend analytics `server_error` / `request_rejected`. Plan writes that lose a race reply with `PLAN_WRITE_CONFLICT_REPLY`. Latency: the flow lock waits up to 5 s per chat (`RedisLock`), and the flight results cache is disabled in this snapshot (commented out in `FlightService`).

**Tell apart.** The same shared-failure text can hide a provider or LLM failure. Prefer `flight_provider` when the search row was saved with no result. Prefer `llm_provider` when an LLM row failed at the same time and the user saw it. Otherwise use this owner.

Files: `bookkrapp/src/modules/chat/ChatController.ts` (`persistSharedFailureMessage`), `bookkrapp/src/modules/utils/userFacingText.ts`, `bookkrapp/src/app.ts`.

## product

**What it covers.** Things Bookkr does not do, by design in this code:
- cabs, buses, trains, activities, day-by-day itineraries (fixed "coming soon" reply);
- flight searches for several routes at once (`MULTI_ROUTE_FLIGHT_REPLY`);
- external partner bookings (Bookkr never learns the outcome);
- no hotel property-type filter column;
- multi-city leg rows without party stamps.

Planning a multi-city trip is supported.

**In the data.** `unsupported_feature_requested` with the feature; fixed replies.

**Tell apart.** A correct "coming soon" for a request Bookkr cannot serve is `product`. A wrong "coming soon" (the user only asked about a provider, or had a single route) is the rule or the check that fired (`other_rule` / `multi_route_check`).

Files: `bookkrapp/src/modules/flow-router/unsupportedFeatures.ts`, `intent/multiRouteFlightCheck.ts`.

## unknown

Use it when the transcript cannot show which component decided first. The common cases:
- a flight_search line that differs from the travel_intent line with no clue whether the cache or the second LLM caused it;
- chips you cannot see;
- a plan action you cannot tie to a turn rule.

LlmCallLog rows in this snapshot carry no chat id (see gaps), so missing failure lines do not prove an LLM succeeded.

## gaps

- **The flow router is not on the default path.** `unified_chat_route` runs only when `UNIFIED_INTENT_ENABLED=false`. With the default `true`, flow comes from the extractor's `actionHint`, `mergeActionHint` revival and `ActionDecider`. The judge prompt's step 2 and SYSTEM_MAP's "Route the turn" box describe the kill-switch path, not the default.
- **The unsupported-feature check runs late.** It runs only after a turn has been routed to general assistance, not before routing as SYSTEM_MAP draws it.
- **LlmCallLog has no chat link.** `persistLLMCall` (`llm/LLMObservability.ts`) writes `userId` and `chatSessionId` as null. The analyst joins `raw_llm_calls` on `chat_session_id`, so with this code no "LLM CALL FAILED" line can reach a transcript. Production may differ (unsure). `fallbackUsed` is not stored, and `error` holds only a reason code.
- **A second flight extractor exists.** `intent_extraction_thread` (`TravelOrchestrator.extractIntentFromHistory`) re-reads every flight turn and writes the flight_search intent. The taxonomy folds it into `unified_intent_extraction`; it may deserve its own owner, or should be read as part of `flight_state_carryover`.
- **Logged tasks missing from TASK_OWNER:** `airport_guidance`, `hotel_amenity_classification`, `chat_session_title` (quiet only), `flight_results_summary` (a log label only; the call is logged as `generate_response`). Event-to-destination resolution makes model calls that are not logged at all.
- **Tasks shared by several jobs.** `multi_city_intent` covers the plan router, planner, Q&A, repair, airport resolution and transfer explanations. `generate_response` covers most prose and the hotel extractor's model choice. LlmCallLog cannot separate them.
- **Suggested-trips tasks.** `suggestion_research` / `suggestion_extract` belong to the suggested-trips feed, not chat web search.
- **Chips are not stored.** Reply chips are not saved on messages, so `nudge_recommendation` friction is mostly invisible.
- **Hotel extractor errors are invisible.** Parse failures fall back silently and are logged as success.
- **Smaller owners the taxonomy lacks:** the group engagement gate, the sticky hotel gate's fixed conditions, the confirmation gate's "already confirmed" rule (currently inside `action_decider`), and the client-fired search step (currently `realtime_client`).
- **No Redis flight cache.** SYSTEM_MAP's "Redis cache" before the gateway is commented out in `FlightService` in this snapshot.
