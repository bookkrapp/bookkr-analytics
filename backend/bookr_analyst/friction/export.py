"""Turn reviewed friction items into files, split by fix lane (rule R8).

- Intent RAG file:          intent items an LLM owns (lane rag or prompt). Few-shot / RAG for the extractor and router prompts.
- Response-quality file:    agent-suggestion items an LLM owns (lane rag or prompt). Examples for the reply, plan, chip prompts.
- Fine-tune queue:          items routed to fine-tuning (the LoRA extractor served the turn, or the miss is structural).
- Engineering list:         technical items and anything a fixed rule, a service, product or nobody-yet owns.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..config import settings
from . import taxonomy as TX
from .signals import config
from .store import all_dicts, conn, now_iso

CONTEXT_TURNS = 6

GROUPS = {
    "intent_rag": {"label": "Intent RAG file", "prefix": "bookkr_intent_rag",
                   "title": "Bookkr intent understanding: lessons from real conversations"},
    "response_quality": {"label": "Response-quality file", "prefix": "bookkr_response_quality",
                         "title": "Bookkr agent suggestions: lessons from real conversations"},
    "finetune": {"label": "Fine-tune queue", "prefix": "bookkr_finetune_queue",
                 "title": "Bookkr intent extractor: fine-tune queue"},
    "engineering": {"label": "Engineering list", "prefix": "bookkr_engineering_list",
                    "title": "Bookkr friction: engineering, product and triage list"},
}


def group_of(it: dict) -> str:
    lane = it.get("fix_lane") or "triage"
    if lane == "finetune":
        return "finetune"
    if lane in ("rag", "prompt"):
        return "response_quality" if it.get("bucket") == "suggestion" else "intent_rag"
    return "engineering"


def _parse_json(s):
    if not s:
        return None
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return s


def dataset_items() -> list[dict]:
    """Every item a reviewer agreed with, newest first, with its finding."""
    with conn() as c:
        return all_dicts(c.execute("""
            SELECT i.*, f.day, f.flow, f.title, f.transcript, f.session_started_local, f.submitted_at
            FROM items i JOIN findings f ON f.id = i.finding_id
            WHERE i.verdict = 'agree' ORDER BY i.reviewed_at DESC""").fetchall())


def build_example(it: dict) -> dict:
    turns = it.get("transcript") or []
    nums = [n for n in (it.get("turns") or []) if isinstance(n, int)]
    anchor = max(nums) if nums else (turns[-1]["t"] if turns else 0)
    # the turns the item points at (e.g. where the info was first given) + the lead-up to the anchor turn
    keep = set(nums) | set(range(anchor - CONTEXT_TURNS // 2, anchor + 1))
    ctx = [dict(t, text=(t["text"] or "")[:600]) for t in turns if t["t"] in keep]
    expected = _parse_json(it.get("corrected_interpretation") or it.get("expected_interpretation"))
    return {
        "id": f"bookkr-fx-{it['id']}",
        "bucket": it.get("bucket"),
        "friction_type": it["type"],
        "owner": it.get("owner"),
        "fix_lane": it.get("fix_lane"),
        "group": group_of(it),
        "lesson_is_rule": bool(it.get("lesson_is_rule")),
        "served_model": it.get("served_model"),
        "severity": it["severity"],
        "flow": it.get("flow"),
        "user_message": it.get("user_quote") or "",
        "context": [{"turn": t["t"], "role": t["role"].lower(),
                     "text": (t["text"] or "") + (f" {t['card']}" if t.get("card") else ""),
                     **({"events": [e["text"] for e in t.get("events") or []]} if t.get("events") else {})}
                    for t in ctx],
        "what_went_wrong": it.get("what_went_wrong") or "",
        "expected_interpretation": expected,
        "guidance": it.get("corrected_guidance") or it.get("guidance") or "",
        "reviewer_note": it.get("review_note") or "",
        "source": {"session_id": it["session_id"], "day": it.get("day"), "reviewed_by": it.get("reviewer"),
                   "reviewed_at": it.get("reviewed_at"), "detected_by": it.get("source")},
    }


def _example_md(n: int, e: dict, expected_label: str) -> list[str]:
    out = ["", f"### Example {n}: {e['friction_type'].replace('_', ' ')} ({e['flow'] or 'unknown flow'})", "Conversation:"]
    for t in e["context"]:
        body = t["text"].strip().replace("\n", "\n> ")
        out.append(f"> **{t['role']} (t{t['turn']}):** {body}\n>")
    out.append(f"\nWhat went wrong: {e['what_went_wrong']}")
    if e["expected_interpretation"]:
        exp = e["expected_interpretation"]
        if isinstance(exp, str) and not exp.lstrip().startswith(("{", "[")):
            out.append(f"{expected_label}: {exp}")
        else:
            out += [f"{expected_label}:", "```json",
                    json.dumps(exp, ensure_ascii=False, indent=2) if not isinstance(exp, str) else exp, "```"]
    if e["guidance"]:
        out.append(f"Rule: {e['guidance']}")
    return out


def to_markdown(examples: list[dict], group: str = "intent_rag") -> str:
    g = GROUPS[group]
    out = [f"# {g['title']}",
           f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from {len(examples)} human-reviewed friction items._", ""]
    if group == "engineering":
        return "\n".join(out + _engineering_md(examples)) + "\n"
    expected_label = "What a good response would have offered" if group == "response_quality" \
        else "Correct interpretation of the user's message"
    if group == "finetune":
        out += ["Each example is a turn the fine-tuned intent extractor (LoRA) got wrong, or a structural miss. "
                "Convert them into realism rows with the generator in `bookkrapp/tests/finetune/intent/`, retrain, "
                "and pass the ship gates in `docs/operations/finetuning.md` before deploying.", ""]
    by_owner: dict[str, list[dict]] = {}
    for e in examples:
        by_owner.setdefault(e.get("owner") or "unknown", []).append(e)
    for owner, exs in by_owner.items():
        out.append(f"## {TX.OWNER_LABEL.get(owner, owner)} (`{owner}`)")
        rules = sorted({e["guidance"].strip() for e in exs if e["lesson_is_rule"] and e["guidance"].strip()})
        if rules:
            out += ["", "**Rules to add to this prompt** (reviewed lessons that must always hold):", *[f"- {r}" for r in rules]]
        patterns = sorted({e["guidance"].strip() for e in exs if not e["lesson_is_rule"] and e["guidance"].strip()})
        if patterns:
            out += ["", "**Guidance:**", *[f"- {r}" for r in patterns]]
        out += ["", "**Examples**"]
        for n, e in enumerate(exs, 1):
            out += _example_md(n, e, expected_label)
        out.append("")
    return "\n".join(out) + "\n"


def _engineering_md(examples: list[dict]) -> list[str]:
    def cell(s):
        return str(s or "").replace("|", "\\|").replace("\n", " ")[:300]
    out = []
    order = [("technical", "Technical"), ("intent", "Intent: caused by a fixed rule, or owner not set yet"),
             ("suggestion", "Agent suggestion: ranking, options, product, or owner not set yet")]
    for b, title in order:
        exs = [e for e in examples if e["bucket"] == b]
        if not exs:
            continue
        out += [f"## {title}", "", "| Severity | Type | Owner | Lane | What went wrong | Note | Chat | Day |", "|---|---|---|---|---|---|---|---|"]
        for e in sorted(exs, key=lambda e: {"high": 0, "medium": 1, "low": 2}.get(e["severity"], 3)):
            out.append(f"| {e['severity']} | {e['friction_type']} | {TX.OWNER_LABEL.get(e['owner'], e['owner'])} | "
                       f"{TX.FIX_LANE_LABEL.get(e['fix_lane'], e['fix_lane'])} | {cell(e['what_went_wrong'])} | "
                       f"{cell(e['guidance'] or e['reviewer_note'])} | `{e['source']['session_id']}` | {e['source']['day']} |")
        out.append("")
    return out


def finetune_candidates(examples: list[dict]) -> list[dict]:
    """Q13: an RAG cluster (same owner and type) big enough that fine-tuning may beat more examples."""
    n_min = int(config()["thresholds"].get("finetune_cluster_min", 20))
    clusters: dict[tuple, int] = {}
    for e in examples:
        if e["group"] == "intent_rag" and e["fix_lane"] == "rag":
            k = (e["owner"], e["friction_type"])
            clusters[k] = clusters.get(k, 0) + 1
    return sorted(({"owner": o, "type": t, "items": n, "threshold": n_min, "ready": n >= n_min}
                   for (o, t), n in clusters.items()), key=lambda x: -x["items"])


def write_export(created_by: str | None) -> dict:
    examples = [build_example(i) for i in dataset_items()]
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    files, counts = [], {}
    for key, g in GROUPS.items():
        exs = [e for e in examples if e["group"] == key]
        counts[key] = len(exs)
        if not exs:
            continue
        jl = settings.exports_dir / f"{g['prefix']}_{stamp}.jsonl"
        md = settings.exports_dir / f"{g['prefix']}_{stamp}.md"
        with open(jl, "w", encoding="utf-8") as f:
            for e in exs:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        md.write_text(to_markdown(exs, key), encoding="utf-8")
        files.append({"group": key, "label": g["label"], "count": len(exs), "jsonl": jl.name, "md": md.name})
    n_rules = len({e["guidance"].strip() for e in examples if e["group"] != "engineering" and e["guidance"].strip()})
    main = next((f for f in files if f["group"] == "intent_rag"), files[0] if files else None)
    with conn() as c:
        cur = c.execute("""INSERT INTO exports (created_at, created_by, examples, rules, jsonl_file, md_file, files, counts)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (now_iso(), created_by, len(examples), n_rules, main["jsonl"] if main else None,
                         main["md"] if main else None, json.dumps(files), json.dumps(counts)))
        eid = cur.lastrowid
    return {"id": eid, "examples": len(examples), "rules": n_rules, "files": files, "counts": counts,
            "jsonl_file": main["jsonl"] if main else None, "md_file": main["md"] if main else None}
