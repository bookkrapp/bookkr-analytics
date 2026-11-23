"""Rule-based friction signals. Cheap, deterministic, always on.

They pick which chats the LLM judge reads, and in rules-only mode they are the findings.
Rule R1 (TRACKER.md): an assistant question asking for a MANDATORY field is never friction;
those asks are recorded as `expected_ask` (weight 0) so reviewers can see they were ignored on purpose.
Rule R8: every signal carries a bucket (technical | intent | suggestion) and an owner (taxonomy.py).
Weight-0 signals are context for the judge and the reviewer; they never become items on their own.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from functools import lru_cache

from ..config import settings
from . import taxonomy as TX

STOP = set("""
a an the to from for of on in at by with and or but is are was were be been am i me my we our you your it this that
these those please pls plz want wanna need would like can could will shall should get give show find search book booking
booked flight flights hotel hotels stay stays trip travel travelling traveling ticket tickets option options plan plans
yes no ok okay sure hi hello hey thanks thank also just only some any more one way round return also via near best cheap
cheapest good nice let lets go going there here what when where which who how much many about around between same
why suggest suggested reach include included check tell know give take make add see look route routes way best fly
""".split())

MONTHS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "january", "february",
          "march", "april", "june", "july", "august", "september", "october", "november", "december"}


@lru_cache(maxsize=1)
def config() -> dict:
    with open(settings.friction_config_path, encoding="utf-8") as f:
        return json.load(f)


def reload_config():
    config.cache_clear()


def _rx(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns if not p.startswith("_")]


def info_tokens(text: str) -> set[str]:
    """Content-bearing tokens: places, dates, numbers, names. Generic travel words removed."""
    toks = re.findall(r"[a-zA-Zऀ-ॿ]+|\d+", (text or "").lower())
    out = set()
    for t in toks:
        if t.isdigit():
            out.add(t)
        elif t in MONTHS:
            out.add(t[:3])
        elif len(t) >= 3 and t not in STOP:
            out.add(t)
    return out


def asked_fields(text: str) -> list[str]:
    cfg = config()["ask_patterns"]
    found = []
    for field, spec in cfg.items():
        if field.startswith("_"):
            continue
        if any(r.search(text or "") for r in _rx(spec["patterns"])):
            found.append(field)
    return found


def is_mandatory(field: str) -> bool:
    spec = config()["ask_patterns"].get(field)
    return bool(spec and spec.get("mandatory"))


def _dt(v) -> datetime | None:
    """db.rows() returns timestamps as ISO strings."""
    if isinstance(v, datetime) or v is None:
        return v
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


def _secs(a, b) -> float | None:
    a, b = _dt(a), _dt(b)
    return (b - a).total_seconds() if a and b else None


def _iata(v) -> str | None:
    if isinstance(v, dict):
        v = v.get("iata") or v.get("code") or v.get("airport_code")
    if isinstance(v, str) and re.fullmatch(r"[A-Za-z]{3}", v.strip()):
        return v.strip().upper()
    return None


def _exact_date(block) -> str | None:
    if isinstance(block, dict):
        d = block.get("exact_date") or block.get("exactDate")
        return str(d)[:10] if d else None
    return None


def _turn_at(turns: list[dict], when) -> int | None:
    """The last USER turn at or before `when` (UTC); the first turn if none."""
    best = None
    for t in turns:
        at = t.get("at_utc")
        if at is None or when is None:
            continue
        if t["role"] == "USER" and at <= when:
            best = t["t"]
    return best or (turns[0]["t"] if turns else None)


def compute(bundle: dict) -> list[dict]:
    cfg = config()
    W = cfg["weights"]
    T = cfg["thresholds"]
    quick = _rx(cfg["quick_reply_patterns"]["patterns"])
    strong = _rx(cfg["correction_markers"]["strong"])
    weak = _rx(cfg["correction_markers"]["weak"])
    tech_err = _rx(cfg["technical_error_patterns"])
    no_res = _rx(cfg["no_results_patterns"])
    past = _rx(cfg["past_date_patterns"])
    gap = _rx(cfg["product_gap_patterns"])
    turns = bundle["turns"]
    out: list[dict] = []

    def add(code, weight, turns_, detail, ftype, severity="medium", owner=None, bucket=None, **extra):
        ftype = TX.normalize_type(ftype) if ftype != "expected" else ftype
        b = bucket or (TX.bucket_for(ftype) if ftype != "expected" else "intent")
        out.append({"code": code, "weight": weight, "turns": [x for x in turns_ if x], "detail": detail, "type": ftype,
                    "bucket": b, "owner": owner or TX.DEFAULT_OWNER.get(ftype, "unknown"),
                    "severity": severity, **extra})

    prior_user: list[tuple[int, set[str], str]] = []
    last_assistant: dict | None = None
    seen_assistant = False
    for t in turns:
        text = t["text"] or ""
        if t["role"] == "ASSISTANT":
            fields = asked_fields(text)
            if fields:
                mand = [f for f in fields if is_mandatory(f)]
                non = [f for f in fields if not is_mandatory(f)]
                if mand:
                    add("expected_ask", 0, [t["t"]], f"Asked for mandatory field(s): {', '.join(mand)} — ignored (rule R1)",
                        "expected", "low", owner="action_decider", fields=mand)
                if non and not mand:
                    add("nonmandatory_reask", W["nonmandatory_reask"], [t["t"]],
                        f"Asked for optional field(s) {', '.join(non)}", "unnecessary_question", "low", fields=non)
            has_search = any(e["kind"] in ("flight_search", "hotel_search") for e in t["events"])
            stored_nothing = any("no result stored" in e["text"] for e in t["events"])
            if t["kind"] == "shared_failure" or any(r.search(text) for r in tech_err):
                add("app_error", W["app_error"], [t["t"]], "Assistant reported an error to the user", "app_error", "medium")
            elif any(r.search(text) for r in no_res):
                if stored_nothing:
                    add("provider_no_result", W["provider_failure"], [t["t"]],
                        "No results and no provider response was stored for the search", "provider_failure", "medium")
                elif has_search:
                    add("no_availability", 0, [t["t"]], "Search ran and returned nothing (may be real no-availability)",
                        "provider_failure", "low")
                else:
                    add("could_not_find", W["could_not_find"], [t["t"]],
                        "Assistant could not find what the user named (no search ran)", "misunderstood_intent", "medium",
                        owner="unknown")
            if any(r.search(text) for r in past):
                add("past_date", W["past_date"], [t["t"]], "Assistant said the dates are in the past", "misunderstood_intent",
                    "medium", owner="date_policy")
            if any(r.search(text) for r in gap):
                add("product_gap_reply", W["product_gap"], [t["t"]], "Assistant said Bookkr doesn't do this yet",
                    "product_gap", "low", owner="product")
            if text.lower().startswith("no problem") and "change" in text.lower():
                add("edit_after_confirm", W["edit_after_confirm"], [t["t"]],
                    "User chose Edit on the intent confirmation (something was captured wrong)", "misunderstood_intent", "low")
            last_assistant = t
            seen_assistant = True
            continue
        if t["role"] != "USER":
            continue
        low = text.strip().lower()
        is_quick = any(r.search(low) for r in quick)
        if seen_assistant and not is_quick:
            if any(r.search(low) for r in strong):
                add("correction_strong", W["correction_strong"], [t["t"]], "User corrected the assistant",
                    "misunderstood_intent", "high")
            elif len(low) < 120 and any(r.search(low) for r in weak):
                add("correction_weak", W["correction_weak"], [t["t"]], "Possible correction / rejection",
                    "misunderstood_intent", "low")
        toks = info_tokens(text)
        if prior_user and not is_quick and len(toks) >= T["repeat_min_overlap_tokens"]:
            earlier = set().union(*(p[1] for p in prior_user))
            overlap = toks & earlier
            if len(overlap) >= T["repeat_min_overlap_tokens"] and len(overlap) / len(toks) >= T["repeat_min_overlap_ratio"]:
                src = [p[0] for p in prior_user if p[1] & overlap]
                after_ask = asked_fields(last_assistant["text"]) if last_assistant else []
                add("repeated_info", W["repeated_info"], src[-2:] + [t["t"]],
                    f"User repeated info already given: {', '.join(sorted(overlap))}"
                    + (f" (in reply to a question about {', '.join(after_ask)})" if after_ask else ""),
                    "repeated_info", "high", repeated=sorted(overlap), after_mandatory_ask=bool(after_ask))
        if not is_quick:
            prior_user.append((t["t"], toks, text))

    # slow replies (technical: latency)
    slow_s = T["slow_reply_s"]
    for i, t in enumerate(turns):
        if t["role"] != "USER" or t.get("at_utc") is None:
            continue
        nxt = next((x for x in turns[i + 1:] if x["role"] == "ASSISTANT"), None)
        if not nxt or nxt.get("at_utc") is None:
            continue
        secs = _secs(t["at_utc"], nxt["at_utc"])
        if secs is not None and secs >= slow_s and nxt["t"] == t["t"] + 1:
            add("slow_reply", W["latency"], [t["t"], nxt["t"]], f"Reply took {int(secs)} s",
                "latency", "medium" if secs >= 2 * slow_s else "low")

    # searches. The same search firing several times for one request is system fan-out, not friction;
    # it only counts when the USER asked again (a new non-quick user message in between).
    hashes: dict[str, int] = {}          # search key -> user turn that triggered it
    flagged_keys: set[str] = set()
    last_user_t = 0
    first_search_t = None
    for t in turns:
        if t["role"] == "USER" and not any(r.search((t["text"] or "").strip().lower()) for r in quick):
            last_user_t = t["t"]
        for e in t["events"]:
            if e["kind"] not in ("flight_search", "hotel_search"):
                continue
            first_search_t = first_search_t or t["t"]
            if e["kind"] != "flight_search":
                continue
            m = re.match(r"FLIGHT SEARCH: (\w+) → (\w+)", e["text"])
            if m and m.group(1) == m.group(2) and "bad" not in flagged_keys:
                flagged_keys.add("bad")
                add("bad_search_params", W["bad_search_params"], [t["t"]],
                    f"Flight search with origin = destination ({m.group(1)})", "wrong_search", "high")
            key = e["text"].rsplit(" → ", 1)[0]  # drop the result count
            if key in hashes and hashes[key] != last_user_t and key not in flagged_keys:
                flagged_keys.add(key)
                add("repeat_search", W["repeat_search"], [hashes[key], last_user_t],
                    "User asked for the same flight search again", "wrong_search", "low")
            hashes.setdefault(key, last_user_t)

    # extractor output vs the search that actually ran (owner: the code between them)
    out.extend(_search_mismatch(bundle, W))
    # the turn's travel intent vs the flight-search intent re-extracted from carried state
    out.extend(_intent_drift(bundle, W))

    # LLM calls: a failure the user saw is technical; a LoRA failure recovered by the fallback is a fine-tune hint
    calls = bundle.get("llm_calls") or []
    for i, c in enumerate(calls):
        if c["success"]:
            continue
        owner = TX.TASK_OWNER.get(c["task"], "llm_provider")
        recovered = any(x["success"] and TX.TASK_OWNER.get(x["task"], x["task"]) == TX.TASK_OWNER.get(c["task"], c["task"])
                        and 0 <= (_secs(c["created_at"], x["created_at"]) or -1) <= T["llm_fallback_window_s"]
                        for x in calls[i + 1:])
        err = (c["error"] or "unknown").strip()
        structural = bool(re.search(r"parse|invalid_enum|schema|json", err, re.I))
        where = [_turn_at(turns, c["created_at"])]
        if recovered or c["task"] in TX.QUIET_TASKS:
            add("llm_failure_recovered", 0, where,
                f"{c['task']} failed on {c['model']} and was retried / fell back: {err[:120]}",
                "llm_call_failure", "low", owner="llm_provider",
                lora_structural=structural and TX.is_lora(c["model"]))
        else:
            add("llm_call_failure", W["llm_failure"], where, f"LLM call {c['task']} failed ({c['model']}): {err[:160]}",
                "llm_call_failure", "medium", owner="llm_provider", task_owner=owner)

    # analytics events and failed bookings (attached to turns by time in transcript.py)
    for t in turns:
        for e in t["events"]:
            txt = e["text"]
            if e["kind"] == "booking_failure":
                if re.search(r"abandon", txt, re.I):  # the user left the payment: not a system failure
                    add("payment_abandoned", 0, [t["t"]], txt[:240], "payment_booking_failure", "low")
                else:
                    add("booking_failed", W["payment_booking_failure"], [t["t"]], txt[:240], "payment_booking_failure", "high")
            elif e["kind"] != "analytics":
                continue
            elif txt.startswith("PAYMENT FAILED"):
                add("payment_failed", W["payment_booking_failure"], [t["t"]], txt, "payment_booking_failure", "high")
            elif txt.startswith("FARE EXPIRED"):
                add("fare_expired", W["fare_expired"], [t["t"]], txt, "payment_booking_failure", "medium")
            elif txt.startswith("PRICE CHANGED"):
                small = "under_100" in txt
                add("price_changed", 0 if small else W["price_changed"], [t["t"]], txt, "payment_booking_failure",
                    "low" if small else "medium")
            elif txt.startswith("UNSUPPORTED FEATURE"):
                add("product_gap", W["product_gap"], [t["t"]], txt, "product_gap", "low", owner="product")
            elif txt.startswith("USER USED"):
                add("changed_filters", W["changed_filters"], [t["t"]],
                    "User changed filters or sort after results (ranking may have missed a preference)",
                    "poor_ranking", "low", owner="ranking")
            elif "0 results" in txt:
                add("zero_results_shown", 0, [t["t"]], txt, "provider_failure", "low")

    for fb in bundle["feedback"]:
        if not fb["is_positive"]:
            tn = next((t["t"] for t in turns if t["msg_id"] == fb["message_id"]), None)
            add("negative_feedback", W["negative_feedback"], [tn] if tn else [],
                f"User gave 👎{': ' + fb['comment'] if fb.get('comment') else ''}", "user_frustration", "high",
                bucket=_feedback_bucket(out, turns, tn))

    # typed user messages (quick-reply buttons excluded) before the first search
    n_before = sum(1 for t in turns if t["role"] == "USER" and (first_search_t is None or t["t"] < first_search_t)
                   and not any(r.search((t["text"] or "").strip().lower()) for r in quick))
    if n_before >= T["long_slot_filling_user_msgs"]:
        add("long_slot_filling", W["long_slot_filling"], [],
            f"{n_before} typed user messages before the first search" if first_search_t
            else f"{n_before} typed user messages and no search ran", "slow_progress", "medium")
    return _merge(out, {"app_error", "correction_weak", "nonmandatory_reask", "expected_ask", "slow_reply",
                        "llm_failure_recovered", "changed_filters", "price_changed", "no_availability",
                        "zero_results_shown", "product_gap", "product_gap_reply", "booking_failed", "payment_abandoned",
                        "payment_failed", "fare_expired", "could_not_find", "past_date", "llm_call_failure",
                        "provider_no_result"})


def _search_mismatch(bundle: dict, W: dict) -> list[dict]:
    """A flight search whose route, exact date or adults differ from the extracted intent just before it."""
    evs = sorted((e for e in bundle["events"] if e["kind"] in ("intent", "flight_search")), key=lambda e: e["at"])
    last_intent = None
    out = []
    seen = set()
    for e in evs:
        if e["kind"] == "intent":
            p = e["raw"].get("parameters")
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except ValueError:
                    p = None
            if isinstance(p, dict) and (p.get("origin") or p.get("destination")) \
                    and str(p.get("trip_type") or p.get("tripType") or "").upper() in ("ONE_WAY", "ROUND_TRIP", ""):
                last_intent = (e["at"], p)
            continue
        if not last_intent or (_secs(last_intent[0], e["at"]) or 0) > 600:
            continue
        p, f = last_intent[1], e["raw"]
        io, idest = _iata(p.get("origin")), _iata(p.get("destination"))
        so, sd = _iata(f.get("origin")), _iata(f.get("destination"))
        if not (io and idest and so and sd):
            continue
        leg = "departure"
        if (so, sd) == (idest, io):
            leg = "return"           # the return leg of a round trip searched on its own
        elif not (so == io or sd == idest):
            continue                 # a leg of a plan between other cities: not this intent's search
        diffs = []
        if leg == "departure":
            if so != io:
                diffs.append(f"origin {io} → searched {so}")
            if sd != idest:
                diffs.append(f"destination {idest} → searched {sd}")
        mode = str(p.get("date_mode") or p.get("dateMode") or "").upper()
        d_int, d_srch = _exact_date(p.get(leg)), str(f.get("departure_date") or "")[:10]
        if mode == "EXACT_DATE" and d_int and d_srch and d_int != d_srch:
            diffs.append(f"{leg} date {d_int} → searched {d_srch}")
        pax = p.get("passengers") if isinstance(p.get("passengers"), dict) else {}
        if pax.get("adults") and f.get("adults") and int(pax["adults"]) != int(f["adults"]):
            diffs.append(f"adults {pax['adults']} → searched {f['adults']}")
        key = "; ".join(diffs)
        if diffs and key not in seen:
            seen.add(key)
            t = _turn_at(bundle["turns"], e["at"])
            out.append({"code": "search_mismatch", "weight": W["search_mismatch"], "turns": [t] if t else [],
                        "detail": f"Search differs from the extracted intent: {key}", "type": "wrong_search",
                        "bucket": "intent", "owner": "search_builder", "severity": "high"})
    return out


def _intent_drift(bundle: dict, W: dict) -> list[dict]:
    """A `flight_search` intent whose origin or destination differs from the `travel_intent` stored just before it.

    The flight phase re-extracts the intent from the previous one (read from Redis / the DB), so a city the
    traveller gave can be swapped for a stale one, e.g. a HYD trip searched as BLR.
    """
    def params(e):
        p = e["raw"].get("parameters")
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except ValueError:
                return None
        return p if isinstance(p, dict) else None

    evs = sorted((e for e in bundle["events"] if e["kind"] == "intent"), key=lambda e: e["at"])
    out, seen, last_travel = [], set(), None
    for e in evs:
        kind = e["raw"].get("intent_type")
        p = params(e)
        if not p:
            continue
        if kind == "travel_intent":
            last_travel = (e["at"], p)
            continue
        if kind != "flight_search" or not last_travel or (_secs(last_travel[0], e["at"]) or 0) > 180:
            continue
        diffs = []
        for k in ("origin", "destination"):
            a, b = _iata(last_travel[1].get(k)), _iata(p.get(k))
            if a and b and a != b:
                diffs.append(f"{k} {a} → {b}")
        key = "; ".join(diffs)
        if diffs and key not in seen:
            seen.add(key)
            t = _turn_at(bundle["turns"], e["at"])
            out.append({"code": "intent_drift", "weight": W["intent_drift"], "turns": [t] if t else [],
                        "detail": f"Flight-search intent differs from the travel intent just captured: {key}",
                        "type": "wrong_search", "bucket": "intent", "owner": "flight_state_carryover", "severity": "high"})
    return out


def _feedback_bucket(sig: list[dict], turns: list[dict], tn: int | None) -> str:
    """Rules-mode guess for a 👎: technical if something broke nearby, suggestion if it rated results/an answer."""
    if tn is None:
        return "intent"
    near = {tn - 1, tn, tn + 1}
    if any(s["bucket"] == "technical" and s["weight"] > 0 and near & set(s["turns"]) for s in sig):
        return "technical"
    t = next((x for x in turns if x["t"] == tn), None)
    if t and (t.get("card_type") or "").endswith(("results", "itinerary", "options")) or (t and not asked_fields(t["text"])):
        return "suggestion"
    return "intent"


def _merge(sig: list[dict], codes: set[str]) -> list[dict]:
    """Collapse repeated signals of the same kind into one (turns combined, weight counted once)."""
    out, seen = [], {}
    for s in sig:
        if s["code"] in codes and s["code"] in seen:
            m = seen[s["code"]]
            m["turns"] = sorted(set(m["turns"]) | set(s["turns"]))
            if s["detail"] not in m["detail"]:
                m["detail"] += f"; {s['detail']}"
            m["n"] = m.get("n", 1) + 1
            continue
        s = dict(s)
        if s["code"] in codes:
            seen[s["code"]] = s
        out.append(s)
    for s in seen.values():
        if s.get("n", 1) > 1 and s["code"] == "app_error":
            s["detail"] = f"Assistant reported an error to the user ({s['n']} times)"
    return out


def score(signals: list[dict]) -> float:
    return float(sum(s["weight"] for s in signals))
