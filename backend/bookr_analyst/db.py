"""Postgres access (Neon). One database, two schemas:

* ``analyst``  : the masked analytics copy, rebuilt and swapped in by every sync (sync.py)
* ``friction`` : Friction Finder runs, findings and human reviews (friction/store.py); never touched by a sync

Every machine that sets the same ANALYST_DATABASE_URL (a laptop, the Render service) sees the same data.
"""
from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlsplit

import psycopg
from psycopg.adapt import Loader
from psycopg_pool import ConnectionPool

from .config import settings

ANALYTICS_SCHEMA = "analyst"
FRICTION_SCHEMA = "friction"
# Bookkr's production database: Bookr Analyst only ever reads it (sync.py, read-only), never writes to it.
PROTECTED_DATABASES = {"bookkrdb"}


class _NumberLoader(Loader):
    """numeric -> int when it is a whole number (sum of ints), else float. Never Decimal."""

    def load(self, data):
        s = bytes(data).decode()
        return int(s) if s.lstrip("-").isdigit() else float(s)


class _TextLoader(Loader):
    """json -> the raw text. Callers parse the columns they need (see rows(json_cols=...))."""

    def load(self, data):
        return bytes(data).decode()


def configure(pg: psycopg.Connection) -> None:
    pg.adapters.register_loader("numeric", _NumberLoader)
    pg.adapters.register_loader("json", _TextLoader)
    pg.adapters.register_loader("jsonb", _TextLoader)


def _db_identity(url: str) -> tuple[str, str] | None:
    """(endpoint host without Neon's -pooler suffix, database name), or None if the URL can't be read."""
    try:
        u = urlsplit(url)
    except ValueError:
        return None
    if not u.hostname:
        return None
    return u.hostname.lower().replace("-pooler.", "."), (u.path.lstrip("/") or "postgres").lower()


def database_url() -> str:
    url = settings.analyst_database_url
    if not url:
        raise RuntimeError("ANALYST_DATABASE_URL is not set. Point it at the Neon database that stores "
                           "Bookr Analyst's own data (see .env.example).")
    ours = _db_identity(url)
    if ours is None:
        raise RuntimeError("ANALYST_DATABASE_URL is not a valid postgresql://user:password@host/database URL.")
    # Never write into Bookkr's own database: the sync and the Friction Finder create schemas and tables here.
    src = _db_identity(settings.source_database_url) if settings.source_database_url else None
    if ours == src or ours[1] in PROTECTED_DATABASES:
        raise RuntimeError(f"ANALYST_DATABASE_URL points at Bookkr's production database ({ours[1]}). Bookr Analyst "
                           "must use its own database; refusing to connect.")
    return url


def open_connection() -> psycopg.Connection:
    """A dedicated connection (used by the sync, which runs one long transaction)."""
    pg = psycopg.connect(database_url(), cursor_factory=psycopg.ClientCursor)
    configure(pg)
    return pg


_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> ConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            # Parameters are bound client-side (ClientCursor), so nothing is prepared on the server and the
            # Neon pooled (-pooler) connection string works. Idle connections are dropped before Neon suspends.
            _pool = ConnectionPool(database_url(), min_size=0, max_size=8, max_idle=120, timeout=30,
                                   kwargs={"cursor_factory": psycopg.ClientCursor}, configure=configure, open=True)
        return _pool


_NAMED = re.compile(r"\$([A-Za-z_]\w*)")


def to_pg(sql: str, params) -> str:
    """The app writes `$name` (dict params) or `?` (positional params); psycopg wants %(name)s / %s."""
    sql = sql.replace("%", "%%")
    return _NAMED.sub(r"%(\1)s", sql) if isinstance(params, dict) else sql.replace("?", "%s")


class Con:
    """Thin wrapper so call sites keep `con.execute(sql, params)` with `$name` or `?` placeholders."""

    def __init__(self, pg: psycopg.Connection, row_factory=None):
        self.pg = pg
        self._rf = row_factory

    def _cursor(self):
        return self.pg.cursor(row_factory=self._rf) if self._rf else self.pg.cursor()

    def execute(self, sql: str, params=None):
        return self._cursor().execute(to_pg(sql, params), params if params is not None else ())

    def executemany(self, sql: str, seq) -> None:
        seq = list(seq)
        if seq:
            self._cursor().executemany(to_pg(sql, seq[0]), seq)


@contextmanager
def session(schema: str, require_table: str | None = None):
    """One transaction with `schema` as the search path. Commits on success, rolls back on error.

    The search path is set per transaction (not per connection) so it also holds behind Neon's pooler.
    """
    last_error: Exception | None = None
    for _ in range(3):
        with _get_pool().connection() as pg:
            try:
                found = pg.execute("SELECT set_config('search_path', %s, true), to_regclass(%s)",
                                   (schema, f"{schema}.{require_table}" if require_table else None)).fetchone()[1]
            except psycopg.OperationalError as e:  # stale pooled connection (Neon suspended): take a fresh one
                last_error = e
                pg.close()
                continue
            if require_table and found is None:
                raise FileNotFoundError("No data synced yet. Run a sync first (python -m bookr_analyst.sync).")
            yield pg
            return
    raise last_error  # type: ignore[misc]


@contextmanager
def connect():
    """Read the analytics copy. Raises FileNotFoundError until the first sync has run."""
    with session(ANALYTICS_SCHEMA, "sync_meta") as pg:
        yield Con(pg)


def is_synced() -> bool:
    try:
        with connect():
            return True
    except FileNotFoundError:
        return False


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
    cur = con.execute(sql, params)
    names = [d[0] for d in cur.description]
    jc = set(json_cols)
    return [{n: _clean(v, jc, n) for n, v in zip(names, r)} for r in cur.fetchall()]


def one(con, sql: str, params=None, json_cols: tuple[str, ...] = ()) -> dict | None:
    r = rows(con, sql, params, json_cols)
    return r[0] if r else None
