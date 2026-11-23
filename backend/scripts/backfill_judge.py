"""Run the LLM judge over a range of days, newest first.

    python scripts/backfill_judge.py 2026-11-02 2026-11-18

Days that already have a finished LLM run are skipped, so it's safe to stop (Ctrl+C) and start again.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from bookr_analyst.friction.runner import run_day  # noqa: E402
from bookr_analyst.friction.store import conn  # noqa: E402


def main():
    start, end = (date.fromisoformat(x) for x in sys.argv[1:3])
    d = end
    while d >= start:
        with conn() as c:
            done = c.execute("SELECT 1 FROM runs WHERE day = ? AND mode = 'llm' AND status = 'done'",
                             (d.isoformat(),)).fetchone()
        if done:
            print(d, "already judged, skipping", flush=True)
        else:
            r = run_day(d, use_llm=True, triggered_by="backfill")
            print(d, json.dumps({k: v for k, v in r.items() if not isinstance(v, (list, dict))}), flush=True)
        d -= timedelta(days=1)


if __name__ == "__main__":
    main()
