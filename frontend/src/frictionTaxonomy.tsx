// Labels for the friction buckets, types, owners and fix lanes (rule R8).
// Keep in step with backend/bookr_analyst/friction/taxonomy.py (the backend is the source of truth for routing).

export const BUCKETS = ["technical", "intent", "suggestion"] as const;
export type Bucket = (typeof BUCKETS)[number];

export const BUCKET_LABEL: Record<string, string> = {
  technical: "Technical",
  intent: "Intent",
  suggestion: "Agent suggestion",
};
export const BUCKET_SHORT: Record<string, string> = {
  technical: "The system broke",
  intent: "Bookkr misread the user",
  suggestion: "Understood, but offered something poor",
};

export const TYPE_BUCKET: Record<string, Bucket | null> = {
  repeated_info: "intent", misunderstood_intent: "intent", missed_info: "intent", wrong_flow: "intent",
  wrong_search: "intent", unnecessary_question: "intent", slow_progress: "intent",
  poor_ranking: "suggestion", poor_plan: "suggestion", poor_answer: "suggestion", irrelevant_chips: "suggestion",
  poor_options: "suggestion", product_gap: "suggestion",
  provider_failure: "technical", llm_call_failure: "technical", payment_booking_failure: "technical",
  latency: "technical", client_delivery: "technical", app_error: "technical",
  user_frustration: null, other: null,
};

export const TYPE_LABEL: Record<string, string> = {
  repeated_info: "Repeated info",
  misunderstood_intent: "Misunderstood intent",
  missed_info: "Missed info",
  wrong_flow: "Wrong flow",
  wrong_search: "Wrong search",
  unnecessary_question: "Unnecessary question",
  slow_progress: "Slow progress",
  poor_ranking: "Poor ranking",
  poor_plan: "Poor plan",
  poor_answer: "Poor answer",
  irrelevant_chips: "Irrelevant chips",
  poor_options: "Poor options",
  product_gap: "Product gap",
  provider_failure: "Provider failure",
  llm_call_failure: "LLM call failure",
  payment_booking_failure: "Payment / booking failure",
  latency: "Slow reply",
  client_delivery: "App delivery",
  app_error: "App error",
  user_frustration: "User frustration",
  other: "Other",
  dead_end: "Dead end",
};

export const OWNER_GROUPS: [string, [string, string][]][] = [
  ["LLM tasks (data can fix it)", [
    ["unified_intent_extraction", "Intent extractor"],
    ["unified_chat_route", "Flow router"],
    ["hotel_intent_extraction", "Hotel extractor / turn scope"],
    ["multi_city_intent", "Trip plans and plan router"],
    ["multi_route_check", "Multi-route check"],
    ["soft_signals", "Preference reader"],
    ["generate_response", "Reply writer"],
    ["web_search", "Web search answer"],
    ["nudge_recommendation", "Reply chips"],
    ["hotel_highlights", "Hotel highlights"],
  ]],
  ["Fixed rules (code fix)", [
    ["date_policy", "Date rules"],
    ["action_decider", "Ask / confirm / search rules"],
    ["airport_choice", "Airport choice rules"],
    ["search_builder", "Search params builder"],
    ["ranking", "Ranking engine"],
    ["date_options", "Date options algorithm"],
    ["plan_turn_rules", "Plan turn rules"],
    ["flight_state_carryover", "Flight state carried over"],
    ["other_rule", "Other fixed rule"],
  ]],
  ["Services (code / ops fix)", [
    ["flight_provider", "Flight providers / gateway"],
    ["hotel_provider", "Hotel providers"],
    ["payments_booking", "Payments and booking"],
    ["llm_provider", "LLM provider"],
    ["realtime_client", "App / realtime delivery"],
    ["backend", "Backend"],
  ]],
  ["Other", [["product", "Product gap"], ["unknown", "Not sure yet"]]],
];
export const OWNER_LABEL: Record<string, string> = Object.fromEntries(OWNER_GROUPS.flatMap(([, o]) => o));
export const OWNER_KIND: Record<string, string> = Object.fromEntries(
  OWNER_GROUPS.flatMap(([g, o]) => o.map(([k]) => [k, g.startsWith("LLM") ? "llm" : g.startsWith("Fixed") ? "rule" : g.startsWith("Services") ? "service" : k === "product" ? "product" : "unknown"])),
);

export const LANE_LABEL: Record<string, string> = {
  engineering: "Engineering",
  prompt: "Prompt change",
  rag: "RAG example",
  finetune: "Fine-tune queue",
  product: "Product",
  triage: "Needs triage",
};

const isLora = (m?: string | null) => !!m && /\/|qwen|nemotron|wandb|lora/i.test(m);

/** Mirror of taxonomy.fix_lane, for the live preview while a reviewer edits the routing. */
export function laneFor(bucket: string, owner: string, servedModel?: string | null, lessonIsRule = false): string {
  const kind = OWNER_KIND[owner] ?? "unknown";
  if (bucket === "technical" || kind === "rule" || kind === "service") return "engineering";
  if (kind === "product") return "product";
  if (kind === "unknown") return "triage";
  if (lessonIsRule) return "prompt";
  if (owner === "unified_intent_extraction" && isLora(servedModel)) return "finetune";
  return "rag";
}

export function shortModel(m?: string | null) {
  if (!m) return null;
  if (/nemotron/i.test(m)) return "Nemotron LoRA";
  if (/qwen|wandb/i.test(m)) return "Qwen LoRA";
  return m.replace(/-\d{4}-\d{2}-\d{2}$/, "");
}

export function BucketPill({ b }: { b?: string | null }) {
  if (!b) return null;
  return <span className={`bpill ${b}`}>{BUCKET_LABEL[b] ?? b}</span>;
}

export function LanePill({ lane }: { lane?: string | null }) {
  if (!lane) return null;
  return <span className={`lane ${lane}`}>{LANE_LABEL[lane] ?? lane}</span>;
}
