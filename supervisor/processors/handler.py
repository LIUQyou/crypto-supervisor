"""
TickHandler v3
==============

* ticker  → price‑move alerts
* trade   → FlowAccumulator
* depth   → SpreadMonitor
* retry‑aware e‑mail dispatch
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Tuple

from supervisor.alerts.engine import AlertEngine
from supervisor.storage.memory import MemoryStore
from supervisor.storage.redis_store import RedisStore

from supervisor.processors.flow import FlowAccumulator
from supervisor.processors.spread import SpreadMonitor
from supervisor.processors.file_sink import FileSink

logger = logging.getLogger(__name__)


class TickHandler:
    RETRY_DELAY_S = 5
    MAX_RETRIES = 2

    # ------------------------------------------------------------------ #
    # init                                                               #
    # ------------------------------------------------------------------ #
    def __init__(self, config: Dict):
        # -------- storage backend ------------------------------------- #
        st_conf = config.get("storage", {})
        backend = st_conf.get("backend", "memory").lower()
        sink_cfg = st_conf.get("sink", {})
        self.file_sink = FileSink(**sink_cfg)   # e.g. rotate_minutes=5
        
        if backend == "redis":
            self.store = RedisStore(
                redis_url=st_conf.get("redis_url", "redis://localhost:6379/0"),
                max_memory_bytes=int(st_conf.get("max_memory_gb", 8)) * 1024**3,
                hot_window_ms=int(st_conf.get("hot_window_hours", 24)) * 3600 * 1000,
            )
        else:
            self.store = MemoryStore()

        # -------- alert engine & helpers ------------------------------ #
        self.alert_engine = AlertEngine(config.get("alerts", {}))

        flow_cfg = config["alerts"].get("flow", {})
        spread_cfg = config["alerts"].get("spread", {})

        self.flow_acc = FlowAccumulator(flow_cfg, self.alert_engine)
        self.spread_mon = SpreadMonitor(spread_cfg, self.alert_engine)

        self._sem: Dict[Tuple[str, str], asyncio.Lock] = {}

    # ------------------------------------------------------------------ #
    # public async API                                                   #
    # ------------------------------------------------------------------ #
    async def handle_tick(self, data: Dict):
        """
        Receives one normalised event dict from any connector.

        * ticker  → {"event":"ticker", ...}
        * trade   → {"event":"trade",  ...}
        * depth   → {"event":"depth",  ...}
        """
        try:
            # 1. persist to file sink
            await self.file_sink.add(data)
            
            event = data.get("event", "ticker")

            # ----- trade: buy/sell imbalance ------------------------- #
            if event == "trade":
                await self.flow_acc.add(data)
                return  # do NOT feed into price alerts

            # ----- depth: spread monitor ----------------------------- #
            if event == "depth":
                await self.spread_mon.add(data)
                return  # do NOT feed into price alerts

            # ----- ticker: price‑move alerts ------------------------- #
            exch: str = data["exchange"]
            sym: str = data["symbol"]
            ts: int = int(data.get("timestamp", 0))

            try:
                price = float(data["price"])
            except (TypeError, ValueError):
                return  # ignore malformed ticker

            # 1. persist latest price
            self.store.update(exch, sym, price, ts)
            logger.debug("Stored %s %s @ %s", exch, sym, price)

            # 2. check price thresholds
            alerts = self.alert_engine.check(exch, sym, price, ts)
            if alerts:
                sem = self._sem.setdefault((exch, sym), asyncio.Lock())
                async with sem:
                    for alert in alerts:
                        await self._dispatch_with_retry(alert)

        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in handle_tick(): %s", exc)

    # ------------------------------------------------------------------ #
    # helpers                                                            #
    # ------------------------------------------------------------------ #
    async def _dispatch_with_retry(self, alert: Dict[str, str]):
        """Send an alert email, retrying on failure."""
        for attempt in range(1 + self.MAX_RETRIES):
            if await self.alert_engine.send(alert):
                return
            logger.warning(
                "E‑mail attempt %d/%d failed – retrying in %ds",
                attempt + 1,
                self.MAX_RETRIES + 1,
                self.RETRY_DELAY_S,
            )
            await asyncio.sleep(self.RETRY_DELAY_S)
        logger.error("Giving up on alert: %s", alert["subject"])
