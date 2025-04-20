"""
TickHandler v3 – central dispatcher with
* price‑move alerts
* flow‑imbalance alerts
* spread‑widening alerts
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

logger = logging.getLogger(__name__)


class TickHandler:
    RETRY_DELAY_S = 5
    MAX_RETRIES = 2

    def __init__(self, config: Dict):
        # ------- storage backend ------------------------------------- #
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

        # ------- alert engine & helpers ------------------------------ #
        self.alert_engine = AlertEngine(config.get("alerts", {}))

        flow_cfg = config["alerts"].get("flow", {})
        spread_cfg = config["alerts"].get("spread", {})

        self.flow_acc = FlowAccumulator(flow_cfg, self.alert_engine)
        self.spread_mon = SpreadMonitor(spread_cfg, self.alert_engine)

        self._sem: Dict[Tuple[str, str], asyncio.Lock] = {}

    # ---------------------------------------------------------------- #
    async def handle_tick(self, data: Dict):
        """
        Accepts one of three normalised events:

        ticker → {"event":"ticker","price":…}
        trade  → {"event":"trade", ...}
        depth  → {"event":"depth", ...}
        """
        try:
            event = data.get("event", "ticker")
            exch = data["exchange"]
            sym = data["symbol"]
            ts = int(data.get("timestamp", 0))

            # ----- route non‑ticker events first --------------------
            if event == "trade":
                await self.flow_acc.add(data)
                return                           # not fed to price alerts
            elif event == "depth":
                await self.spread_mon.add(data)
                # continue into price section only if we have a usable price

            # ----- ticker (or depth w/ price) for price alerts -------
            try:
                price = float(data["price"])
            except (TypeError, ValueError):
                return  # skip if price missing

            # persist latest price
            self.store.update(exch, sym, price, ts)
            logger.debug("Stored %s %s @ %s", exch, sym, price)

            # price‑move alerts
            alerts = self.alert_engine.check(exch, sym, price, ts)
            if alerts:
                sem = self._sem.setdefault((exch, sym), asyncio.Lock())
                async with sem:
                    for alert in alerts:
                        await self._dispatch_with_retry(alert)

        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in handle_tick(): %s", exc)

    # ---------------------------------------------------------------- #
    async def _dispatch_with_retry(self, alert: Dict[str, str]):
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
