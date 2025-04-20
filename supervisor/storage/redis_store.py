"""
RedisStore  –  hybrid tick store: hot data in RAM, cold data in Redis.

Requirements
------------
pip install redis psutil
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional, Tuple

import psutil          # to sample process memory
import redis

# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _redis_key(exchange: str, symbol: str) -> str:
    return f"tick:{exchange}:{symbol}"


# --------------------------------------------------------------------------- #
# store implementation                                                         #
# --------------------------------------------------------------------------- #
class RedisStore:
    """
    Keeps *latest* tick for every pair in an in‑process dict for speed.
    When the Python process exceeds ``max_memory_bytes`` (default 8 GiB),
    any entry whose timestamp is older than ``hot_window_ms`` is flushed
    to Redis and removed from the local dict.
    """

    def __init__(
        self,
        redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        max_memory_bytes: int = 8 * 1024 * 1024 * 1024,  # 8 GiB
        hot_window_ms: int = 24 * 3600 * 1000,            # 24 h
    ):
        self._local: Dict[Tuple[str, str], Dict[str, float | int]] = {}
        self._r = redis.from_url(redis_url)
        self._max_bytes = max_memory_bytes
        self._hot_ms = hot_window_ms

    # ------------------------------------------------------------------ #
    # public API (same signature as MemoryStore)                          #
    # ------------------------------------------------------------------ #
    def update(
        self,
        exchange: str,
        symbol: str,
        price: float,
        timestamp: Optional[int] = None,
    ):
        ts = timestamp or int(time.time() * 1000)
        self._local[(exchange, symbol)] = {"price": price, "timestamp": ts}
        self._maybe_evict()

    def get_latest(self, exchange: str, symbol: str):
        # 1️⃣ RAM first
        item = self._local.get((exchange, symbol))
        if item:
            return item

        # 2️⃣ otherwise try Redis
        raw = self._r.get(_redis_key(exchange, symbol))
        if raw is None:
            return None
        return json.loads(raw)

    def get_all(self):
        """
        Snapshot of currently *hot* data (RAM only, quick).
        """
        return {
            ex: {
                sym: data
                for (ex_, sym), data in self._local.items()
                if ex_ == ex
            }
            for ex, _ in self._local.keys()
        }

    # ------------------------------------------------------------------ #
    # internal helpers                                                   #
    # ------------------------------------------------------------------ #
    def _maybe_evict(self):
        """
        If this process is using > max_memory_bytes, move cold entries
        (older than hot_window_ms) to Redis and delete them locally.
        """
        proc = psutil.Process()
        if proc.memory_info().rss < self._max_bytes:
            return

        cutoff = int(time.time() * 1000) - self._hot_ms
        victims = [
            key for key, data in self._local.items() if data["timestamp"] < cutoff
        ]

        if not victims:
            return

        with self._r.pipeline() as pipe:
            for ex, sym in victims:
                data = self._local.pop((ex, sym))
                pipe.set(_redis_key(ex, sym), json.dumps(data))
            pipe.execute()
