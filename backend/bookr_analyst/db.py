import json
import threading
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

import duckdb

from .config import settings

# Held while a request reads the DB and while sync swaps the file in.
db_lock = threading.RLock()


@contextmanager
def connect():
    with db_lock:
        if not settings.analytics_db_path.exists():
            raise FileNotFoundError("Analytics DB not found. Run a sync first (python -m bookr_analyst.sync).")
        con = duckdb.connect(str(settings.analytics_db_path), read_only=True)
        try:
            yield con
        finally:
            con.close()


def _clean(v, json_cols: set[str], name: str):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if name in json_cols and isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def rows(con, sql: str, params=None, json_cols: tuple[str, ...] = ()) -> list[dict]:
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    jc = set(json_cols)
    return [{n: _clean(v, jc, n) for n, v in zip(names, r)} for r in cur.fetchall()]


def one(con, sql: str, params=None, json_cols: tuple[str, ...] = ()) -> dict | None:
    r = rows(con, sql, params, json_cols)
    return r[0] if r else None
