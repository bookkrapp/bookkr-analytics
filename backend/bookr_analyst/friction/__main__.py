"""CLI:  python -m bookr_analyst.friction [--day YYYY-MM-DD] [--rules-only] [--sync]

Default day = yesterday (IST). --sync refreshes data from the source database first.
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
        from ..db import FRICTION_SCHEMA, session
        dest = f"{FRICTION_SCHEMA}_archived_{datetime.now():%Y%m%d_%H%M%S}"
        with session("public") as pg:
            if pg.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (FRICTION_SCHEMA,)).fetchone():
                pg.execute(f"ALTER SCHEMA {FRICTION_SCHEMA} RENAME TO {dest}")
                print(f"Archived schema {FRICTION_SCHEMA} -> {dest}. The next run starts with an empty review queue.")
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
