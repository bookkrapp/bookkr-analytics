"""Service status rows, ported from the old ops board (ops-dashboard/src/checks.js in the Bookkr repo).

Each check pings a service or reads its credit balance. A check whose env vars aren't set reads "not set up", never
"down". Keys stay server-side and are never returned; error messages are our own ("HTTP 503") or a network error.
Results are cached for a minute so the page can be reloaded freely.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import ssl
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from .db import connect

TIMEOUT_S = 20
SLOW_MS = 3000
LOW_CREDIT_SHARE = 0.1
CACHE_S = 60


def _get_json(url: str, headers: dict | None = None, data: bytes | None = None, method: str | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "bookr-analyst", **(headers or {})}, data=data, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 (fixed https/http service URLs from env)
        if r.status >= 400:
            raise RuntimeError(f"HTTP {r.status}")
        body = r.read()
    return json.loads(body) if body else {}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _gateway_token(env) -> str:
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({"sub": "bookr-analyst", "iss": env["TRAVEL_GATEWAY_JWT_ISSUER"], "aud": "travel-gateway",
                               "actorType": "service", "svc": True, "iat": now, "exp": now + 60}).encode())
    sig = hmac.new(env["TRAVEL_GATEWAY_JWT_SECRET"].encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(sig)}"


def _redis_ping(url: str) -> None:
    u = urlparse(url)
    sock = socket.create_connection((u.hostname, u.port or 6379), timeout=TIMEOUT_S)
    if u.scheme == "rediss":
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
    try:
        def cmd(*parts):
            sock.sendall(("*%d\r\n" % len(parts) + "".join(f"${len(p.encode())}\r\n{p}\r\n" for p in parts)).encode())
            reply = sock.recv(256)
            if reply.startswith(b"-"):
                raise RuntimeError("Redis refused the command")
            return reply
        if u.password:
            cmd("AUTH", *([u.username] if u.username and u.username != "default" else []), u.password)
        if not cmd("PING").startswith(b"+PONG"):
            raise RuntimeError("No PONG")
    finally:
        sock.close()


def _strip(url: str) -> str:
    return url.rstrip("/")


GATEWAY_ENV = ["TRAVEL_GATEWAY_BASE_URL", "TRAVEL_GATEWAY_JWT_SECRET", "TRAVEL_GATEWAY_JWT_ISSUER"]


def _analyst_db(env):
    with connect() as con:
        con.execute("SELECT 1")
    return {}


def _searchapi(env):
    acc = _get_json("https://www.searchapi.io/api/v1/me", {"Authorization": f"Bearer {env['SEARCH_API_KEY']}"}).get("account") or {}
    limit, used = acc.get("monthly_allowance"), acc.get("current_month_usage")
    unused = max(0, limit - used) if isinstance(limit, (int, float)) and isinstance(used, (int, float)) else None
    return {"left": max(acc.get("remaining_credits") or 0, unused or 0), "limit": limit}


def _tavily(env):
    u = _get_json("https://api.tavily.com/usage", {"Authorization": f"Bearer {env['TAVILY_API_KEY']}"})
    limit = (u.get("account") or {}).get("plan_limit") or (u.get("key") or {}).get("limit")
    used = (u.get("account") or {}).get("plan_usage") or (u.get("key") or {}).get("usage")
    return {"left": limit - used, "limit": limit} if isinstance(limit, (int, float)) and isinstance(used, (int, float)) else {}


def _serpapi(env):
    a = _get_json(f"https://serpapi.com/account.json?api_key={env['SERP_API_KEY']}")
    return {"left": a.get("total_searches_left", a.get("plan_searches_left")), "limit": a.get("searches_per_month")}


def _tripjack_hotels(env):
    r = _get_json(f"{_strip(env['TRAVEL_GATEWAY_BASE_URL'])}/v1/providers/tripjack-hms/proxy", method="POST", data=b"{}",
                  headers={"Content-Type": "application/json", "Authorization": f"Bearer {_gateway_token(env)}",
                           "x-upstream-path": "/hms/v3/nationality-info", "x-mapping-mode": "mapped"})
    return {} if isinstance(r.get("nationalities"), list) and r["nationalities"] else {"degraded": "Empty reply"}


CHECKS = [
    {"id": "backend", "name": "Bookkr API", "critical": True, "env": ["BACKEND_URL"],
     "run": lambda env: (_get_json(f"{_strip(env['BACKEND_URL'])}/health"), {})[1]},
    {"id": "analyst_db", "name": "Analytics database", "critical": True, "env": [], "run": _analyst_db},
    {"id": "redis", "name": "Redis", "critical": True, "env": ["REDIS_URL"], "run": lambda env: (_redis_ping(env["REDIS_URL"]), {})[1]},
    {"id": "gateway", "name": "Travel gateway", "critical": True, "env": ["TRAVEL_GATEWAY_BASE_URL"],
     "run": lambda env: (_get_json(f"{_strip(env['TRAVEL_GATEWAY_BASE_URL'])}/health"), {})[1]},
    {"id": "payments", "name": "Payments service", "critical": True, "env": ["PAYMENTS_SERVICE_URL"],
     "run": lambda env: (_get_json(f"{_strip(env['PAYMENTS_SERVICE_URL'])}/health"), {})[1]},
    {"id": "tripjack-hotels", "name": "TripJack hotels", "critical": True, "env": GATEWAY_ENV, "run": _tripjack_hotels},
    {"id": "serpapi", "name": "SerpAPI credits", "env": ["SERP_API_KEY"], "run": _serpapi},
    {"id": "searchapi", "name": "SearchAPI credits", "env": ["SEARCH_API_KEY"], "run": _searchapi},
    {"id": "tavily", "name": "Tavily credits", "env": ["TAVILY_API_KEY"], "run": _tavily},
    {"id": "openai", "name": "OpenAI", "env": ["OPENAI_API_KEY"],
     "run": lambda env: (_get_json("https://api.openai.com/v1/models", {"Authorization": f"Bearer {env['OPENAI_API_KEY']}"}), {})[1]},
    {"id": "ably", "name": "Ably (realtime)", "env": [], "run": lambda env: (_get_json("https://rest.ably.io/time"), {})[1]},
]
# TripJack flights is judged from real traffic on the old board (ProviderCallLog), which production doesn't have yet.
NOT_POSSIBLE = [{"id": "tripjack-flights", "name": "TripJack flights",
                 "detail": "Needs the provider call log (not in production yet); never probed, to protect the look-to-book ratio"}]


def _run(check: dict, env: dict) -> dict:
    row = {"id": check["id"], "name": check["name"], "critical": bool(check.get("critical")),
           "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    missing = [k for k in check["env"] if not env.get(k)]
    if missing:
        return {**row, "status": "unset", "detail": f"Set {', '.join(missing)} on the server"}
    t = time.time()
    try:
        out = check["run"](env) or {}
        ms = round((time.time() - t) * 1000)
        left, limit = out.get("left"), out.get("limit")
        low = isinstance(left, (int, float)) and isinstance(limit, (int, float)) and limit > 0 and left / limit < LOW_CREDIT_SHARE
        empty = isinstance(left, (int, float)) and left <= 0
        status = "down" if empty else "slow" if (ms > SLOW_MS or low or out.get("degraded")) else "up"
        note = "Out of credits" if empty else "Credits low" if low else out.get("degraded")
        return {**row, "status": status, "latency_ms": ms, "left": left, "limit": limit, "detail": note}
    except Exception as e:  # noqa: BLE001  never echo a key: our own messages or the exception type only
        msg = str(e) if str(e).startswith("HTTP ") else type(e).__name__
        return {**row, "status": "down", "latency_ms": round((time.time() - t) * 1000), "detail": msg}


def overall(rows: list[dict]) -> dict:
    live = [r for r in rows if r["status"] != "unset"]
    if not live:
        return {"level": "unknown", "label": "NO DATA"}
    if any(r["critical"] and r["status"] == "down" for r in live):
        return {"level": "down", "label": "SERVICE DISRUPTION"}
    if any(r["critical"] and r["status"] == "unset" for r in rows):
        return {"level": "slow", "label": "CHECKS NOT SET UP"}
    if any(r["status"] != "up" for r in live):
        return {"level": "slow", "label": "DELAYS"}
    return {"level": "up", "label": "ALL SYSTEMS GO"}


_cache: dict = {"at": 0.0, "out": None}
_lock = threading.Lock()


def status(force: bool = False) -> dict:
    with _lock:
        if not force and _cache["out"] and time.time() - _cache["at"] < CACHE_S:
            return _cache["out"]
    env = dict(os.environ)
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as ex:
        rows = list(ex.map(lambda c: _run(c, env), CHECKS))
    out = {"rows": rows, "overall": overall(rows), "not_possible": NOT_POSSIBLE}
    with _lock:
        _cache.update(at=time.time(), out=out)
    return out
