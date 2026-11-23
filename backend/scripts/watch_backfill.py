"""Follow the judge backfill: after each newly judged day, ask the server to group items into issues.
When `stop_day` has been judged, stop the backfill process, finish grouping, then write the last-7-days and
weekly briefs and rebuild the intent RAG pack.

    python scripts/watch_backfill.py 2026-11-13
"""
import json
import subprocess
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookr_analyst.friction.store import conn  # noqa: E402

API = "http://127.0.0.1:8000/api/friction"
sys.stdout.reconfigure(encoding="utf-8")


def call(path, body=None):
    req = urllib.request.Request(API + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def judged_days():
    with conn() as c:
        return {r[0] for r in c.execute("SELECT day FROM runs WHERE mode = 'llm' AND status = 'done'")}


def wait_grouping():
    while True:
        time.sleep(15)
        g = call("/periods")["grouping"]
        if not g["running"]:
            return g


def main():
    stop_day = sys.argv[1]
    seen = judged_days()
    print("already judged:", sorted(seen), flush=True)
    call("/issues/group", {})
    while stop_day not in seen:
        time.sleep(30)
        now = judged_days()
        for d in sorted(now - seen):
            print(time.strftime("%H:%M"), "judged", d, "-> grouping", flush=True)
            call("/issues/group", {})
        seen = now
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*backfill_judge.py*' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], check=False)
    print(time.strftime("%H:%M"), f"{stop_day} judged; backfill stopped", flush=True)
    wait_grouping()
    call("/issues/group", {})
    print("grouping:", wait_grouping(), flush=True)
    last = max(seen)
    week_start = (date.fromisoformat(last) - timedelta(days=date.fromisoformat(last).weekday())).isoformat()
    for period, start in (("last7", (date.fromisoformat(last) - timedelta(days=6)).isoformat()), ("week", week_start)):
        call("/briefs", {"period": period, "start": start})
        while True:
            time.sleep(10)
            s = call(f"/briefs/status?period={period}&start={start}")
            if s["status"] != "running":
                print("brief", period, start, s["status"], flush=True)
                break
    print("rag pack:", {k: v for k, v in call("/rag-pack", {}).items() if k != "files"}, flush=True)


if __name__ == "__main__":
    main()
