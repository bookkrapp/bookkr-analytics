"""Run the whole daily pipeline for one IST date, without an API key.

    python scripts/run_day.py 2026-11-22
    python scripts/run_day.py 2026-11-22 --skip-sync     # reuse the data already synced

Does what the daily scheduler does (friction_api.start_daily_scheduler), for the date you pass:
  1. sync from the source database (replaces the `analyst` schema for everyone sharing the database)
  2. Friction Finder for that day: rule signals, then the LLM judge
  3. group the items into issues, write the briefs (day, last 7 days; weekly on a Sunday, monthly on a
     month's last day) and refresh the intent RAG pack

The judge, the grouping and the briefs run through the Claude Code CLI (`claude -p`) on whatever account it
is logged into, so no ANTHROPIC_API_KEY is needed. A date in the future is refused. Running a day again
re-judges its chats, except the ones a reviewer has already given a verdict on.
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from bookr_analyst.config import settings  # noqa: E402
from bookr_analyst.friction import issues as IS  # noqa: E402
from bookr_analyst.friction import judge as J  # noqa: E402
from bookr_analyst.friction.runner import run_day  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Sync, judge one day of chats, group issues, write briefs and the RAG pack.")
    ap.add_argument("day", help="IST date to process, YYYY-MM-DD")
    ap.add_argument("--skip-sync", action="store_true", help="Don't refresh from the source database first")
    a = ap.parse_args()

    try:
        day = date.fromisoformat(a.day)
    except ValueError:
        sys.exit(f"error: {a.day!r} is not a date. Use YYYY-MM-DD.")
    today = (datetime.now(timezone.utc) + timedelta(minutes=settings.tz_offset_minutes)).date()
    if day > today:
        sys.exit(f"error: {day} is in the future (today is {today} IST).")
    if J.provider() != "subscription":
        sys.exit("error: this script runs the judge through the Claude Code CLI, which is not available here "
                 f"(judge backend: {J.provider_label()}). Install Claude Code, run `claude` once to log in, "
                 "leave ANTHROPIC_API_KEY empty and set CLAUDE_CLI_PATH if `claude` is not on PATH.")

    if not a.skip_sync:
        from bookr_analyst.sync import run_sync
        print("Syncing from the source database…", flush=True)
        r = run_sync()
        print(f"  synced in {r['duration_s']}s: {r['row_counts']['raw_chat_sessions']:,} chats, "
              f"{r['row_counts']['raw_messages']:,} messages", flush=True)

    print(f"Friction Finder for {day} ({J.model_name()} · {J.provider_label()})…", flush=True)
    run = run_day(day, use_llm=True, triggered_by="script")
    print("  " + json.dumps(run), flush=True)

    print("Grouping into issues, writing briefs and the RAG pack…", flush=True)
    IS.after_daily_run(day)
    left = IS.state()
    if left["unassigned"] or left["error"]:
        print(f"  warning: {left['unassigned']} item(s) not grouped into an issue. Last error: {left['error']}", flush=True)
    print(f"Done: {run['scanned']} chats scanned, {run['flagged']} flagged, {run['items']} items.")


if __name__ == "__main__":
    main()
