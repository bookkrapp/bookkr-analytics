"""Friction buckets, types, owners and fix lanes (TRACKER rule R8, SYSTEM_MAP.html diagrams 4-5).

Every friction item gets:
- bucket:   what the user ran into. technical | intent | suggestion
- type:     the specific kind of friction (each type has a default bucket)
- owner:    the component that made the FIRST wrong decision (an LLM task, a fixed rule, a service, product)
- fix_lane: where the fix goes. engineering | prompt | rag | finetune | product | triage

Only LLM-owned items reach the RAG / fine-tune files. Everything else goes to the engineering list.
"""
from __future__ import annotations

BUCKETS = {
    "technical": "The system broke: a provider, LLM call, payment or booking step failed, a reply was very slow, or the app didn't show it.",
    "intent": "Bookkr misread the user: a value was captured wrong or dropped, the turn went to the wrong flow, or the user had to repeat or correct themselves.",
    "suggestion": "Bookkr understood, but what it offered was poor: ranking, trip plans, answers, reply chips, airport or date options, or something Bookkr can't do yet.",
}
BUCKET_LABEL = {"technical": "Technical", "intent": "Intent", "suggestion": "Agent suggestion"}

# type -> (bucket, description). Bucket None = the judge / reviewer decides (cross-bucket types).
TYPES: dict[str, tuple[str | None, str]] = {
    # intent
    "repeated_info": ("intent", "User had to repeat information they had already given clearly (the extractor missed or dropped it)."),
    "misunderstood_intent": ("intent", "A wrong value or wrong intent was captured (city, date, party, trip type) and the user had to correct it."),
    "missed_info": ("intent", "Information or a preference the user gave was ignored or dropped, without the user repeating it yet."),
    "wrong_flow": ("intent", "The turn was sent to the wrong flow (e.g. a cab question handled as a hotel search, a plan edit treated as a new plan)."),
    "wrong_search": ("intent", "A search ran with parameters that don't match what the user asked for."),
    "unnecessary_question": ("intent", "Asked for something that is NOT a mandatory field and was not needed, or was already known."),
    "slow_progress": ("intent", "Too many turns to reach a search because of how the assistant handled the conversation (not because the user was undecided)."),
    # suggestion
    "poor_ranking": ("suggestion", "Results were right for the request but ordered or filtered against the user's stated preference (cheapest, morning, non-stop, area)."),
    "poor_plan": ("suggestion", "A trip plan or itinerary was unrealistic, repetitive or ignored what the user asked for."),
    "poor_answer": ("suggestion", "A reply or travel answer was wrong, ungrounded, unhelpful, too long or off-tone."),
    "irrelevant_chips": ("suggestion", "Reply chips (suggested replies) were irrelevant, repetitive or misleading."),
    "poor_options": ("suggestion", "Airport choices, date options or alternatives offered didn't fit the request."),
    "product_gap": ("suggestion", "The user asked for something Bookkr doesn't do (cabs, trains, buses, activities, multi-route flights)."),
    # technical
    "provider_failure": ("technical", "A flight or hotel provider (SerpAPI, Tripjack, travel-gateway) failed, timed out or returned nothing when it should have."),
    "llm_call_failure": ("technical", "An LLM call failed, timed out or returned output that couldn't be parsed, and the user saw the effect."),
    "payment_booking_failure": ("technical", "Payment failed, the fare expired, the price changed, or the booking failed after payment."),
    "latency": ("technical", "The user waited too long for a reply."),
    "client_delivery": ("technical", "The reply or card never reached the app, or an older app build couldn't draw it."),
    "app_error": ("technical", "'Something went wrong', a crash or another backend error."),
    # cross-bucket
    "user_frustration": (None, "User expressed frustration or gave a thumbs-down. Pick the bucket from what caused it."),
    "other": (None, "Any other friction. Pick the bucket from what caused it."),
}
LEGACY_TYPE = {"dead_end": "app_error"}  # items stored before R8

OWNER_KIND = {
    # LLM tasks (the fix can be data: prompt, RAG or fine-tuning)
    "unified_intent_extraction": "llm",
    "unified_chat_route": "llm",
    "hotel_intent_extraction": "llm",
    "multi_city_intent": "llm",
    "multi_route_check": "llm",
    "soft_signals": "llm",
    "generate_response": "llm",
    "web_search": "llm",
    "nudge_recommendation": "llm",
    "hotel_highlights": "llm",
    # fixed rules in bookkrapp (the fix is code)
    "date_policy": "rule",
    "action_decider": "rule",
    "airport_choice": "rule",
    "search_builder": "rule",
    "ranking": "rule",
    "date_options": "rule",
    "plan_turn_rules": "rule",
    "flight_state_carryover": "rule",
    "other_rule": "rule",
    # services and infrastructure (the fix is code / ops)
    "flight_provider": "service",
    "hotel_provider": "service",
    "payments_booking": "service",
    "llm_provider": "service",
    "realtime_client": "service",
    "backend": "service",
    # product
    "product": "product",
    "unknown": "unknown",
}
OWNER_LABEL = {
    "unified_intent_extraction": "Intent extractor (LLM)",
    "unified_chat_route": "Flow router (LLM)",
    "hotel_intent_extraction": "Hotel extractor / turn scope (LLM)",
    "multi_city_intent": "Trip plans and plan router (LLM)",
    "multi_route_check": "Multi-route check (LLM)",
    "soft_signals": "Preference reader (LLM)",
    "generate_response": "Reply writer (LLM)",
    "web_search": "Web search answer (LLM)",
    "nudge_recommendation": "Reply chips (LLM)",
    "hotel_highlights": "Hotel highlights (LLM)",
    "date_policy": "Date rules (past dates, exact window, mixed trip)",
    "action_decider": "Ask / confirm / search rules",
    "airport_choice": "Airport choice rules",
    "search_builder": "Search params builder",
    "ranking": "Ranking engine",
    "date_options": "Date options algorithm",
    "plan_turn_rules": "Plan turn rules (what a turn does while plans are on screen)",
    "flight_state_carryover": "Flight search state carried from earlier turns / cache",
    "other_rule": "Other fixed rule",
    "flight_provider": "Flight providers / gateway",
    "hotel_provider": "Hotel providers",
    "payments_booking": "Payments and booking",
    "llm_provider": "LLM provider (OpenAI / W&B)",
    "realtime_client": "App / realtime delivery",
    "backend": "Backend",
    "product": "Product gap",
    "unknown": "Not sure yet",
}

# LlmCallLog.task -> owner (current names and the older names still in the data)
TASK_OWNER = {
    "unified_intent_extraction": "unified_intent_extraction",
    "intent_extraction": "unified_intent_extraction",
    "intent_extraction_thread": "unified_intent_extraction",
    "unified_chat_route": "unified_chat_route",
    "hotel_intent_extraction": "hotel_intent_extraction",
    "multi_city_intent": "multi_city_intent",
    "multi_route_check": "multi_route_check",
    "soft_signals": "soft_signals",
    "soft_signal_extraction": "soft_signals",
    "generate_response": "generate_response",
    "visible_thread": "generate_response",
    "chat_response": "generate_response",
    "chat_thread_response": "generate_response",
    "web_search_category": "web_search",
    "web_search_grounded": "web_search",
    "suggestion_research": "web_search",
    "suggestion_extract": "web_search",
    "nudge_recommendation": "nudge_recommendation",
    "hotel_highlights": "hotel_highlights",
}
# tasks whose failure the user never sees directly (background)
QUIET_TASKS = {"chat_session_title", "soft_signals", "soft_signal_extraction"}

FIX_LANES = {
    "engineering": "Engineering ticket. A service, the app or a fixed rule made the first wrong decision, so it never enters the dataset.",
    "prompt": "Prompt or policy change in the LLM task that owns it: the lesson is a rule that must always hold.",
    "rag": "RAG / few-shot example retrieved into that LLM task's prompt.",
    "finetune": "Fine-tuning queue for the intent extractor: the LoRA model served the turn, or the miss is structural (schema, lost keys, state across turns).",
    "product": "Product decision: Bookkr doesn't do this yet.",
    "triage": "Owner unclear. A reviewer picks the owner, then the lane follows.",
}
FIX_LANE_LABEL = {"engineering": "Engineering", "prompt": "Prompt change", "rag": "RAG example",
                  "finetune": "Fine-tune queue", "product": "Product", "triage": "Needs triage"}

# where the default owner comes from when a rule signal (not the judge) creates an item
DEFAULT_OWNER = {
    "repeated_info": "unified_intent_extraction",
    "misunderstood_intent": "unified_intent_extraction",
    "missed_info": "unified_intent_extraction",
    "wrong_flow": "unified_chat_route",
    "wrong_search": "unknown",
    "unnecessary_question": "action_decider",
    "slow_progress": "unknown",
    "poor_ranking": "ranking",
    "poor_plan": "multi_city_intent",
    "poor_answer": "generate_response",
    "irrelevant_chips": "nudge_recommendation",
    "poor_options": "unknown",
    "product_gap": "product",
    "provider_failure": "flight_provider",
    "llm_call_failure": "llm_provider",
    "payment_booking_failure": "payments_booking",
    "latency": "backend",
    "client_delivery": "realtime_client",
    "app_error": "backend",
    "user_frustration": "unknown",
    "other": "unknown",
}


def is_lora(model: str | None) -> bool:
    """The fine-tuned intent model (Qwen / Nemotron LoRA on W&B or any HF-style slashed id) vs OpenAI."""
    m = (model or "").lower()
    return bool(m) and ("/" in m or "qwen" in m or "nemotron" in m or "wandb" in m or "lora" in m)


def normalize_type(t: str | None) -> str:
    t = LEGACY_TYPE.get(t or "", t or "other")
    return t if t in TYPES else "other"


def bucket_for(type_: str, given: str | None = None) -> str:
    """The type's bucket wins, except for cross-bucket types where the given bucket (judge/reviewer) is used."""
    fixed = TYPES.get(normalize_type(type_), (None, ""))[0]
    if fixed:
        return fixed
    return given if given in BUCKETS else "intent"


def normalize_owner(owner: str | None) -> str:
    return owner if owner in OWNER_KIND else "unknown"


def fix_lane(bucket: str, owner: str, served_model: str | None = None, lesson_is_rule: bool = False,
             structural: bool = False) -> str:
    """Diagram 5 of SYSTEM_MAP.html."""
    kind = OWNER_KIND.get(owner, "unknown")
    if bucket == "technical" or kind in ("rule", "service"):
        return "engineering"
    if kind == "product":
        return "product"
    if kind == "unknown":
        return "triage"
    if lesson_is_rule:
        return "prompt"
    if owner == "unified_intent_extraction" and (is_lora(served_model) or structural):
        return "finetune"
    return "rag"


def describe() -> dict:
    """For the API / Rules tab."""
    return {
        "buckets": [{"key": k, "label": BUCKET_LABEL[k], "description": v} for k, v in BUCKETS.items()],
        "types": [{"key": k, "bucket": b, "description": d} for k, (b, d) in TYPES.items()],
        "owners": [{"key": k, "kind": OWNER_KIND[k], "label": OWNER_LABEL[k]} for k in OWNER_KIND],
        "fix_lanes": [{"key": k, "label": FIX_LANE_LABEL[k], "description": v} for k, v in FIX_LANES.items()],
    }
