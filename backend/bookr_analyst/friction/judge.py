"""LLM judge: reads one masked transcript and returns structured friction items.

Uses Claude via the official Anthropic SDK with structured outputs, so the reply is always valid JSON.
Enabled when ANTHROPIC_API_KEY is set (or FRICTION_USE_LLM=on with another Anthropic credential).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

from ..config import settings
from . import taxonomy as TX
from .signals import config

FRICTION_TYPES = {k: d for k, (_b, d) in TX.TYPES.items()}

PRICES = {  # USD per 1M tokens: input, output, cache write, cache read
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["has_friction", "summary", "items"],
    "properties": {
        "has_friction": {"type": "boolean"},
        "summary": {"type": "string", "description": "One or two sentences: what the user wanted and where understanding broke down (or 'No friction')."},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["bucket", "type", "owner", "lesson_is_rule", "severity", "turns", "user_quote",
                             "what_went_wrong", "why_friction", "expected_interpretation", "guidance", "confidence"],
                "properties": {
                    "bucket": {"type": "string", "enum": list(TX.BUCKETS)},
                    "type": {"type": "string", "enum": list(FRICTION_TYPES)},
                    "owner": {"type": "string", "enum": list(TX.OWNER_KIND)},
                    "lesson_is_rule": {"type": "boolean"},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "turns": {"type": "array", "items": {"type": "integer"}},
                    "user_quote": {"type": "string"},
                    "what_went_wrong": {"type": "string"},
                    "why_friction": {"type": "string"},
                    "expected_interpretation": {"type": "string"},
                    "guidance": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}


def _claude_cli() -> str | None:
    """Path to the Claude Code CLI (`claude`), used to run the judge on a Claude Pro/Max subscription."""
    if settings.claude_cli_path:
        return settings.claude_cli_path if os.path.exists(settings.claude_cli_path) else None
    found = shutil.which("claude")
    if found:
        return found
    for p in (os.path.expanduser(r"~\.local\bin\claude.exe"), os.path.expandvars(r"%APPDATA%\npm\claude.cmd"),
              os.path.expanduser("~/.local/bin/claude"), "/usr/local/bin/claude", "/opt/homebrew/bin/claude"):
        if os.path.exists(p):
            return p
    return None


def provider() -> str | None:
    """Which judge backend to use: 'api' (ANTHROPIC_API_KEY), 'subscription' (Claude Code CLI login), or None."""
    want = settings.friction_llm_provider
    if settings.friction_use_llm == "off":
        return None
    if want == "api":
        return "api" if settings.anthropic_api_key or settings.friction_use_llm == "on" else None
    if want == "subscription":
        return "subscription" if _claude_cli() else None
    if settings.anthropic_api_key:
        return "api"
    if _claude_cli():
        return "subscription"
    return "api" if settings.friction_use_llm == "on" else None


def llm_enabled() -> bool:
    return provider() is not None


def provider_label() -> str:
    p = provider()
    return {"api": "Anthropic API key", "subscription": "Claude subscription (via Claude Code)"}.get(p, "off")


def model_name() -> str:
    return config()["llm"]["model"]


def build_system(known_false_positives: list[dict]) -> str:
    cfg = config()
    mand = cfg["mandatory_fields"]
    mand_txt = "\n".join(f"- {flow}: {', '.join(fields)}" for flow, fields in mand.items() if not flow.startswith("_"))
    types_txt = "\n".join(
        f"- {k} [{b or 'any bucket'}]: {d}" for k, (b, d) in TX.TYPES.items())
    owners_txt = "\n".join(f"- {k} ({TX.OWNER_KIND[k]}): {TX.OWNER_LABEL[k]}" for k in TX.OWNER_KIND)
    fp_txt = "\n".join(
        f"- [{fp['type']}] {fp['what_went_wrong']}" + (f" — reviewer: {fp['review_note']}" if fp.get("review_note") else "")
        for fp in known_false_positives) or "- (none yet)"
    return f"""You review conversations between users and Bookkr, a conversational travel-booking assistant in India (flights, hotels, multi-city trip plans). Users write in English, Hindi or Hinglish, often informally ("kal", "next friday", "BLR", "Bombay").

Your job: find FRICTION — moments where the user lost time or effort — and sort each one into a bucket and the component that caused it. Reviewed findings are routed by that sorting: LLM-caused ones become few-shot / RAG examples or fine-tuning data, everything else becomes an engineering ticket. A wrong owner sends bad data into training, so precision matters more than recall.

## The three buckets (rule R8)
- technical: the system broke. A provider, LLM call, payment, fare or booking step failed, a reply was very slow, the app showed an error, or a reply/card never reached the user. Lines like LLM CALL FAILED, BOOKING FAILED, PAYMENT FAILED, FARE EXPIRED, PRICE CHANGED and "something went wrong" are evidence.
- intent: Bookkr misread the user. A value was captured wrong or dropped, the turn went to the wrong flow, a search ran with values the user didn't give, or the user had to repeat or correct themselves.
- suggestion: Bookkr understood correctly, but what it OFFERED was poor: results ranked against a stated preference, an unrealistic plan, a wrong or ungrounded answer, irrelevant reply chips, odd airport/date options, or a request Bookkr can't serve (product gap).
Ask in this order and stop at the first yes: did something break (technical)? was the user's meaning captured wrong (intent)? was the offer poor (suggestion)? A broken provider can look like a misunderstanding, so check technical first.

## How a Bookkr turn works (to find the owner)
1. A hotel card still open may claim the turn (hotel extractor `turnScope`, an LLM). Cab/bus/train/activity requests get a fixed "coming soon" reply.
2. `unified_chat_route` (LLM) picks the flow: flights, trip plans, general question.
3. `unified_intent_extraction` (LLM: a fine-tuned Qwen/Nemotron LoRA, with gpt-4.1-mini as fallback) writes the travel intent. The transcript's "EXTRACTED INTENT" lines show what was stored.
4. FIXED RULES then change it: past dates move forward, the exact-window rule, mixed round trips, airport choice (date_policy, airport_choice); the ask / confirm / search decision and the follow-up question wording (action_decider); search params (search_builder).
5. Providers search (flight_provider / hotel_provider), the ranking engine orders results (ranking), LLMs write the reply (generate_response), plans (multi_city_intent), web answers (web_search) and reply chips (nudge_recommendation).
Owner = the FIRST component that went wrong. Compare three things: what the user said, the EXTRACTED INTENT lines, and the FLIGHT/HOTEL SEARCH line. User words wrong in the intent → the extractor (or router) owns it. Intent right but the search differs → a fixed rule owns it (search_builder / date_policy / airport_choice). Search right but the offer was poor → the ranker or the LLM that wrote it. Use "unknown" when you can't tell; never guess an LLM owner.

Two traps that are NOT LLM mistakes:
- A flight turn usually stores TWO intent lines: "EXTRACTED INTENT (travel_intent …)" from the unified extractor, then "EXTRACTED INTENT (flight_search …)" re-built from the previous intent carried in Redis / the DB. If the travel_intent line has the user's value and the flight_search line has a different one (a city swapped for BLR, origin and destination reversed), the owner is `flight_state_carryover`, not `unified_intent_extraction`. Only blame the extractor when the travel_intent line itself is wrong.
- While trip plans are on screen, fixed turn rules decide what a turn does: a flights request that also changes the dates, origin, travellers or destination regenerates the plans instead of searching flights. If a "show me flights" turn returns plans, the owner is `plan_turn_rules`, not `unified_chat_route`, unless no plans were on screen.

One item per root cause. When one mistake causes several moments (a wrong search, then the user restating the cities, then a second wrong search), write ONE item for the first wrong decision and put every affected turn in `turns`. Separate items only when the causes differ.

## Bookkr's workflow and rule R1 (from the Bookkr team)
Bookkr must collect these MANDATORY fields before it can move to the next step:
{mand_txt}
Bookkr then shows an intent confirmation card ("Here is what I have so far. Would you like to proceed?").
- The assistant ASKING for a mandatory field — including asking for it again — and the confirmation step are the designed workflow. Never report them as friction on their own.
- BUT if the user had already given that value clearly and has to type it again because it was not captured, report that as `repeated_info`: the extraction missed it.
- Not friction: the user changing their mind or starting a new plan, quick-reply/button messages ("Yes, search flights"), greetings and small talk, the user supplying a missing mandatory field for the first time, correct "no availability" results, an LLM failure that was silently retried with no visible effect, a small price change under ₹100, or a self-correction inside a single user message that the assistant then handled correctly.

## Friction types (the bucket in brackets must match; "any bucket" types take the bucket of their cause)
{types_txt}

## Owners
{owners_txt}

## How to fill each item
- bucket, type, owner: as above. The type must belong to the bucket.
- lesson_is_rule: true when the lesson is a rule the owner must ALWAYS follow (e.g. "never suggest dates in the past", "say cabs are coming soon") rather than a pattern to learn from examples. Rules become prompt changes; patterns become RAG / fine-tune examples.
- turns: the [tN] numbers where the friction shows (the user's original statement and the turn where it broke).
- user_quote: the user message the owner should have handled, copied verbatim (masked tokens like [phone] stay as they are). "" for technical items with no relevant user message.
- what_went_wrong: what happened, concretely, in one sentence.
- why_friction: why this cost the user effort, in one sentence.
- expected_interpretation:
  - intent: compact JSON (as a string) of what intent extraction should have produced for user_quote, using Bookkr travel_intent field names where relevant: origin{{name,iata}}, destination{{name,iata}}, departure{{exact_date|start_date,end_date|month}}, return{{...}}, tripType (ONE_WAY|ROUND_TRIP|MULTI_CITY), passengers{{adults,children,infants}}, cabin, budget, groupType, notes; for a wrong flow, {{"flow": "..."}}. Include only the fields involved. Resolve relative dates against the message timestamps shown.
  - suggestion: one or two sentences on what a good response would have offered.
  - technical: "".
- guidance: ONE general lesson for the owner that would have prevented this (e.g. "Treat 'kal' as tomorrow relative to the message date", "When the user says 'cheapest', rank by total price"). For technical items, a short engineering note ("Tripjack book failed with errCode 1015: payment amount mismatch"). No user-specific details, no personal names.
- confidence: 0–1, how sure you are this is real friction a reviewer would agree with, with this bucket and owner.
- severity: high = user had to repeat/correct a mandatory field, got a wrong search, or a payment/booking failed; medium = extra turns, ignored preference, a failed step they recovered from; low = minor.
Write [name] instead of any personal name. If there is no friction, return has_friction=false and items=[].

## Known false positives (reviewers marked these "not friction" — do not report similar cases)
{fp_txt}
"""


def _user_prompt(transcript_text: str, signals: list[dict], facts: dict) -> str:
    hints = "\n".join(f"- {s['code']} [{s.get('bucket', '?')} · owner {s.get('owner', '?')}] at turns {s['turns']}: {s['detail']}"
                      for s in signals) or "- none"
    client = f" · app={facts.get('platform')} {facts.get('app_version') or ''}".rstrip() if facts.get("platform") else ""
    return (f"Chat started {facts.get('created_local')} IST · flow={facts.get('current_flow')} · furthest stage={facts.get('stage')}{client}\n\n"
            f"Rule-based hints (may be wrong; weight-0 hints such as 'expected_ask' (R1), 'no_availability' and "
            f"'llm_failure_recovered' are context, NOT friction on their own):\n{hints}\n\n"
            f"Conversation:\n{transcript_text}")


def judge(client, system: str, transcript_text: str, signals: list[dict], facts: dict) -> dict:
    user = _user_prompt(transcript_text, signals, facts)
    if isinstance(client, ClaudeCodeClient):
        return client.judge(system, user)
    cfg = config()["llm"]
    resp = client.beta.messages.create(
        model=cfg["model"],
        max_tokens=8000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": cfg.get("effort", "medium"),
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined to review this conversation")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("judge output was cut off (max_tokens)")
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
    if text is None:
        raise RuntimeError("judge returned no text block")
    out = json.loads(text)
    u = resp.usage
    p = PRICES.get(cfg["model"], PRICES["claude-opus-5-5"])
    inp, outp = u.input_tokens or 0, u.output_tokens or 0
    cw, cr = getattr(u, "cache_creation_input_tokens", 0) or 0, getattr(u, "cache_read_input_tokens", 0) or 0
    out["_usage"] = {"input_tokens": inp + cw + cr, "output_tokens": outp,
                     "cost_usd": (inp * p[0] + outp * p[1] + cw * p[2] + cr * p[3]) / 1e6}
    return out


def make_client():
    if provider() == "subscription":
        return ClaudeCodeClient(_claude_cli())
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key or None, max_retries=3)


# Claude subscription backend -------------------------------------------------

def _validate(out: dict) -> dict:
    """Make the CLI's JSON match SCHEMA (the API path gets this guarantee from structured outputs)."""
    if not isinstance(out, dict) or not isinstance(out.get("has_friction"), bool) or not isinstance(out.get("items"), list):
        raise ValueError("judge reply is missing has_friction/items")
    items = []
    for it in out["items"]:
        if not isinstance(it, dict):
            continue
        t = TX.normalize_type(it.get("type"))
        sev = it.get("severity") if it.get("severity") in ("high", "medium", "low") else "medium"
        turns = [int(str(x).lstrip("t")) for x in (it.get("turns") or []) if str(x).lstrip("t").isdigit()]
        exp = it.get("expected_interpretation", "")
        items.append({"type": t, "bucket": it.get("bucket"), "owner": it.get("owner"),
                      "lesson_is_rule": bool(it.get("lesson_is_rule")), "severity": sev, "turns": turns,
                      "user_quote": str(it.get("user_quote") or ""), "what_went_wrong": str(it.get("what_went_wrong") or ""),
                      "why_friction": str(it.get("why_friction") or ""),
                      "expected_interpretation": exp if isinstance(exp, str) else json.dumps(exp, ensure_ascii=False),
                      "guidance": str(it.get("guidance") or ""),
                      "confidence": float(it["confidence"]) if isinstance(it.get("confidence"), (int, float)) else None})
    return {"has_friction": out["has_friction"] and bool(items), "summary": str(out.get("summary") or ""), "items": items}


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"no JSON object in judge reply: {text[:200]!r}")
    return json.loads(text[start:end + 1])


class ClaudeCodeClient:
    """Runs the judge through the Claude Code CLI in print mode, so it uses whatever account Claude Code is
    logged into (e.g. a Claude Pro/Max subscription) instead of an API key. Usage counts against that
    plan's limits; nothing is billed per token."""

    def __init__(self, cli: str):
        self.cli = cli
        # empty working dir: no project files, CLAUDE.md or settings get pulled into the judge's context
        self.cwd = tempfile.mkdtemp(prefix="bookr-judge-")

    def judge(self, system: str, user: str) -> dict:
        cfg = config()["llm"]
        prompt = (f"{system}\n\n## Output\nReply with ONLY a JSON object (no prose, no code fences) matching this JSON Schema:\n"
                  f"{json.dumps(SCHEMA)}\n\n## Conversation to review\n{user}")
        cmd = [self.cli, "-p", "--output-format", "json", "--model", cfg.get("subscription_model", cfg["model"]),
               "--max-turns", "1", "--disallowedTools", "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task"]
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              cwd=self.cwd, env=env, timeout=int(cfg.get("subscription_timeout_s", 300)))
        if proc.returncode != 0 and not proc.stdout.strip():
            raise RuntimeError(f"claude CLI failed ({proc.returncode}): {(proc.stderr or '').strip()[:300]}")
        try:
            env_out = json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError(f"unexpected claude CLI output: {proc.stdout[:300]!r}")
        if env_out.get("is_error"):
            raise RuntimeError(f"claude CLI error: {str(env_out.get('result') or env_out.get('subtype'))[:300]}")
        out = _validate(_extract_json(env_out.get("result") or ""))
        u = env_out.get("usage") or {}
        out["_usage"] = {"input_tokens": int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0))
                         + int(u.get("cache_creation_input_tokens", 0)),
                         "output_tokens": int(u.get("output_tokens", 0)),
                         "cost_usd": 0.0}  # covered by the subscription
        return out


def ask_json(prompt: str, max_tokens: int = 16000, timeout_s: int | None = None) -> dict:
    """One-shot prompt that must answer with a JSON object. Used by the issue register and the analyst brief.
    Goes through the same account as the judge (Claude subscription CLI or API key)."""
    cfg = config()["llm"]
    if provider() == "subscription":
        cli = _claude_cli()
        cmd = [cli, "-p", "--output-format", "json", "--model", cfg.get("subscription_model", cfg["model"]),
               "--max-turns", "1", "--disallowedTools", "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task"]
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              cwd=tempfile.mkdtemp(prefix="bookr-analyst-"), env=env,
                              timeout=timeout_s or max(600, int(cfg.get("subscription_timeout_s", 300))))
        if proc.returncode != 0 and not proc.stdout.strip():
            raise RuntimeError(f"claude CLI failed ({proc.returncode}): {(proc.stderr or '').strip()[:300]}")
        env_out = json.loads(proc.stdout)
        if env_out.get("is_error"):
            raise RuntimeError(f"claude CLI error: {str(env_out.get('result') or env_out.get('subtype'))[:300]}")
        return _extract_json(env_out.get("result") or "")
    if not llm_enabled():
        raise RuntimeError("LLM is not configured")
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None, max_retries=3)
    with client.messages.stream(model=cfg["model"], max_tokens=max_tokens,
                                messages=[{"role": "user", "content": prompt}]) as s:
        resp = s.get_final_message()
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    return _extract_json(text)


def check_subscription() -> dict:
    """Quick health check used by the status endpoint: is the CLI installed and logged in?"""
    cli = _claude_cli()
    if not cli:
        return {"cli": None, "ok": False, "detail": "Claude Code CLI not found"}
    try:
        v = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30)
        return {"cli": cli, "ok": v.returncode == 0, "version": (v.stdout or "").strip()}
    except Exception as e:  # noqa: BLE001
        return {"cli": cli, "ok": False, "detail": str(e)}
