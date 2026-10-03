"""Tiny SQLite key/value cache with TTL, plus a cached async HTTP client."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

DAY = 86400


class Cache:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, expires REAL)"
        )
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            row = self._db.execute("SELECT v, expires FROM kv WHERE k=?", (key,)).fetchone()
        if not row or row[1] < time.time():
            return None
        return json.loads(row[0])

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO kv VALUES (?,?,?)",
                (key, json.dumps(value), time.time() + ttl),
            )
            self._db.commit()


class Http:
    """httpx wrapper: caching, bounded concurrency, and a couple of retries."""

    def __init__(self, cache: Cache, concurrency: int = 8, timeout: float = 30):
        self.cache = cache
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "health-provider-discovery/0.1 (personal use)"},
        )
        self.sem = asyncio.Semaphore(concurrency)

    async def close(self) -> None:
        await self.client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        files: dict | None = None,
        data: dict | None = None,
        ttl: float = 7 * DAY,
        parse: str = "json",
        cache_key: str | None = None,
    ) -> Any:
        key = cache_key or hashlib.sha256(
            json.dumps([method, url, params, json_body], sort_keys=True, default=str).encode()
        ).hexdigest()
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        last: Exception | None = None
        for attempt in range(3):
            try:
                async with self.sem:
                    r = await self.client.request(
                        method, url, params=params, json=json_body, files=files, data=data
                    )
                if r.status_code in (429, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                r.raise_for_status()
                out = r.json() if parse == "json" else r.text
                self.cache.set(key, out, ttl)
                return out
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last = e
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 429:
                    break
                await asyncio.sleep(1.5 * (attempt + 1))
        raise last  # type: ignore[misc]

    async def get(self, url: str, **kw) -> Any:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw) -> Any:
        return await self.request("POST", url, **kw)
