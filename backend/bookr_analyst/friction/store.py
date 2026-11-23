"""Persistent store for Friction Finder runs, findings and human reviews.

Lives in its own SQLite file (data/friction.sqlite) so a data refresh — which
replaces analytics.duckdb — never wipes reviews.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from ..config import settings

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,              -- running | done | failed
  mode TEXT NOT NULL,                -- llm | rules
  model TEXT,
  triggered_by TEXT,
  sessions_scanned INTEGER DEFAULT 0,
  sessions_judged INTEGER DEFAULT 0,
  sessions_flagged INTEGER DEFAULT 0,
  sessions_skipped_reviewed INTEGER DEFAULT 0,
  items INTEGER DEFAULT 0,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  progress TEXT,
  error TEXT
);
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  day TEXT NOT NULL,
  session_id TEXT NOT NULL,
  user_id TEXT,
  title TEXT,
  flow TEXT,
  session_started_local TEXT,
  n_user_msgs INTEGER,
  outcome TEXT,
  score REAL,
  source TEXT,                       -- llm | rules
  summary TEXT,
  signals TEXT,                      -- JSON list
  transcript TEXT,                   -- JSON list (masked snapshot)
  created_at TEXT NOT NULL,
  submitted_at TEXT,
  submitted_by TEXT
);
CREATE INDEX IF NOT EXISTS findings_day ON findings(day);
CREATE INDEX IF NOT EXISTS findings_session ON findings(session_id);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id INTEGER NOT NULL,
  session_id TEXT NOT NULL,
  type TEXT NOT NULL,
  severity TEXT NOT NULL,
  turns TEXT,                        -- JSON list of transcript turn numbers
  user_quote TEXT,
  what_went_wrong TEXT,
  why_friction TEXT,
  expected_interpretation TEXT,
  guidance TEXT,
  confidence REAL,
  source TEXT,                       -- llm | rules
  verdict TEXT NOT NULL DEFAULT 'pending',   -- pending | agree | not_friction | skip
  review_note TEXT,
  corrected_interpretation TEXT,
  corrected_guidance TEXT,
  reviewer TEXT,
  reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS items_finding ON items(finding_id);
CREATE TABLE IF NOT EXISTS exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  created_by TEXT,
  examples INTEGER,
  rules INTEGER,
  jsonl_file TEXT,
  md_file TEXT
);
-- Issue register: named root causes that judged items are grouped into (issues.py)
CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT,
  bucket TEXT,
  owner TEXT,
  fix_lane TEXT,
  recommended_fix TEXT,
  lesson TEXT,                       -- rule for the owning prompt (LLM-owned issues only)
  status TEXT NOT NULL DEFAULT 'open',   -- open | in_progress | fixed | wont_fix | not_issue
  status_note TEXT,
  status_by TEXT,
  status_at TEXT,
  merged_into INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  period TEXT NOT NULL,              -- day | week | month
  start TEXT NOT NULL,
  "end" TEXT NOT NULL,
  created_at TEXT NOT NULL,
  triggered_by TEXT,
  model TEXT,
  content TEXT,                      -- JSON written by the analyst model
  stats TEXT                         -- JSON numbers the brief was written from
);
"""


MIGRATIONS = {  # table -> columns added after the first release (R8)
    "items": {"bucket": "TEXT", "owner": "TEXT", "fix_lane": "TEXT", "served_model": "TEXT",
              "lesson_is_rule": "INTEGER DEFAULT 0", "routing_edited": "INTEGER DEFAULT 0",
              "issue_id": "INTEGER", "issue_attempts": "INTEGER DEFAULT 0"},
    "exports": {"files": "TEXT", "counts": "TEXT"},
    "issues": {"theme": "TEXT"},
}
_migrated = False


def _migrate(c: sqlite3.Connection) -> None:
    global _migrated
    if _migrated:
        return
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # backfill items stored before R8: bucket and owner from the type, then the lane
    from . import taxonomy as TX
    for r in c.execute("SELECT id, type, bucket, owner, served_model, lesson_is_rule FROM items WHERE fix_lane IS NULL").fetchall():
        t = TX.normalize_type(r["type"])
        b = r["bucket"] or TX.bucket_for(t)
        o = TX.normalize_owner(r["owner"] or TX.DEFAULT_OWNER.get(t))
        c.execute("UPDATE items SET type = ?, bucket = ?, owner = ?, fix_lane = ? WHERE id = ?",
                  (t, b, o, TX.fix_lane(b, o, r["served_model"], bool(r["lesson_is_rule"])), r["id"]))
    c.execute("CREATE INDEX IF NOT EXISTS items_bucket ON items(bucket)")
    c.execute("CREATE INDEX IF NOT EXISTS items_issue ON items(issue_id)")
    _migrated = True


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def conn():
    with _lock:
        settings.friction_db_path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(settings.friction_db_path)
        c.row_factory = sqlite3.Row
        try:
            c.executescript(SCHEMA)
            _migrate(c)
            yield c
            c.commit()
        finally:
            c.close()


JSON_FIELDS = {"signals", "transcript", "turns", "files", "counts", "content", "stats"}


def as_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_FIELDS & d.keys():
        if d[k]:
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def all_dicts(rows) -> list[dict]:
    return [as_dict(r) for r in rows]
