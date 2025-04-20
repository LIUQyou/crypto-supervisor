"""
TickHandler v2  –  central dispatcher with retry & error isolation
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Tuple

from supervisor.alerts.engine import AlertEngine
from supervisor.storage.memory import MemoryStore
from supervisor.storage.redis_store import RedisStore

logger = logging.getLogger(__name__)

class TickHandler:
    """
    Consumes normalised ticks, persists them, and fires alerts.
    """

    RETRY_DELAY_S = 5          # wait before retrying a failed e‑mail
    MAX_RETRIES   = 2          # total attempts = 1 original + MAX_RETRIES

    def __init__(self, config):
        # ------------- choose storage backend -------------------------- #
        storage_conf = config.get("storage", {})
        backend = storage_conf.get("backend", "memory").lower()

        if backend == "redis":
            self.store = RedisStore(
                redis_url=storage_conf.get("redis_url", "redis://localhost:6379/0"),
                max_memory_bytes=int(storage_conf.get("max_memory_gb", 8)) * 1024**3,
                hot_window_ms=int(storage_conf.get("hot_window_hours", 24)) * 3600 * 1000,
            )
        else:
            self.store = MemoryStore()

        # ------------- alert engine & per‑pair semaphore --------------- #
        self.alert_engine = AlertEngine(config.get("alerts", {}))
        self._sem: Dict[Tuple[str, str], asyncio.Lock] = {}

    # ------------------------------------------------------------------ #
    # public async API                                                   #
    # ------------------------------------------------------------------ #
    async def handle_tick(self, data: Dict):
        """
        Receive a tick of the form
            {"exchange": str, "symbol": str, "price": float, "timestamp": int}

        Stores it, checks thresholds, and dispatches alerts.
        """
        try:
            exch: str = data["exchange"]
            sym: str = data["symbol"]
            try:
                price = float(data["price"])
            except (TypeError, ValueError):
                # Ignore events that don't have a usable price field
                return
            ts = int(data.get("timestamp", 0))

            # 1. persist
            self.store.update(exch, sym, price, ts)
            logger.debug("Stored %s %s @ %s", exch, sym, price)

            # 2. alert check
            alerts = self.alert_engine.check(exch, sym, price, ts)
            if alerts:
                # guarantee single serialized e‑mail flow per (exch, sym)
                sem = self._sem.setdefault((exch, sym), asyncio.Lock())
                async with sem:
                    for alert in alerts:
                        await self._dispatch_with_retry(alert)

        except Exception as exc:  # noqa: BLE001
            # defensive: never let a bad tick crash the stream
            logger.exception("Error in handle_tick(): %s", exc)

    # ------------------------------------------------------------------ #
    # helpers                                                            #
    # ------------------------------------------------------------------ #
    async def _dispatch_with_retry(self, alert: Dict[str, str]):
        """
        Try to send an alert e‑mail, retrying on failure.
        """
        for attempt in range(1 + self.MAX_RETRIES):
            ok = await self.alert_engine.send(alert)
            if ok:
                return
            logger.warning(
                "E‑mail attempt %d/%d failed – will retry in %ds",
                attempt + 1,
                self.MAX_RETRIES + 1,
                self.RETRY_DELAY_S,
            )
            await asyncio.sleep(self.RETRY_DELAY_S)
        logger.error("Giving up on alert: %s", alert["subject"])
