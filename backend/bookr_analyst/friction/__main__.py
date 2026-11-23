"""CLI:  python -m bookr_analyst.friction [--day YYYY-MM-DD] [--rules-only] [--sync]

Default day = yesterday (IST). --sync refreshes data from Neon first.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone

from ..config import settings
from .runner import run_day


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="IST date to analyse, YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--rules-only", action="store_true", help="Skip the LLM judge")
    ap.add_argument("--sync", action="store_true", help="Sync from Neon before analysing")
    ap.add_argument("--reset", action="store_true",
                    help="Archive the friction store (findings + reviews) and start empty, e.g. when moving from test to production data")
    a = ap.parse_args()
    if a.reset:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        f = settings.friction_db_path
        if f.exists():
            dest = f.with_name(f"{f.stem}.archived-{stamp}{f.suffix}")
            f.rename(dest)
            print(f"Archived {f.name} -> {dest.name}. The next run starts with an empty review queue.")
        else:
            print("Nothing to reset.")
        return
    if a.sync:
        from ..sync import run_sync
        run_sync()
    if a.day:
        day = datetime.strptime(a.day, "%Y-%m-%d").date()
    else:
        day = (datetime.now(timezone.utc) + timedelta(minutes=settings.tz_offset_minutes)).date() - timedelta(days=1)
    print(json.dumps(run_day(day, use_llm=False if a.rules_only else None), indent=2))


if __name__ == "__main__":
    main()
