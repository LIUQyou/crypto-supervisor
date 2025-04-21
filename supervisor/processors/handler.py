# supervisor/processors/handler.py   –   TickHandler v4
# ------------------------------------------------------
# * ticker  → price‑move alerts (unchanged)
# * trade   → FlowImbalanceMonitor
# * depth   → SpreadStressMonitor [+ QueueImbalanceMonitor]
# * writes every tick to FileSink
# * supports Redis or in‑memory store
# * retry‑aware e‑mail dispatch

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Tuple

from supervisor.alerts.engine        import AlertEngine
from supervisor.storage.memory       import MemoryStore
from supervisor.storage.redis_store  import RedisStore
from supervisor.processors.file_sink import FileSink

# --- enhanced processors ----------------------------------------------------
from supervisor.processors.flow_monitor   import FlowImbalanceMonitor
from supervisor.processors.spread_monitor import SpreadStressMonitor
from supervisor.processors.queue_monitor  import QueueImbalanceMonitor  # optional

logger = logging.getLogger(__name__)


class TickHandler:
    RETRY_DELAY_S = 5
    MAX_RETRIES   = 2

    # ------------------------------------------------------------------ #
    # init                                                               #
    # ------------------------------------------------------------------ #
    def __init__(self, config: Dict):
        # ---------- storage backend (latest price cache) -------------- #
        st_conf  = config.get("storage", {})
        backend  = st_conf.get("backend", "memory").lower()
        sink_cfg = st_conf.get("sink", {})
        self.file_sink = FileSink(**sink_cfg)

        if backend == "redis":
            self.store = RedisStore(
                redis_url       = st_conf.get("redis_url", "redis://localhost:6379/0"),
                max_memory_bytes= int(st_conf.get("max_memory_gb", 8)) * 1024 ** 3,
                hot_window_ms   = int(st_conf.get("hot_window_hours", 24)) * 3600 * 1000,
            )
        else:
            self.store = MemoryStore()

        # ---------- alert engine & processors -------------------------- #
        self.alert_engine = AlertEngine(config.get("alerts", {}))

        flow_cfg   = config["alerts"].get("flow",   {})
        spread_cfg = config["alerts"].get("spread", {})
        queue_cfg  = config["alerts"].get("queue",  {   # optional new block
            "threshold":   0.7,
            "window_ms":   3000,
            "cooldown_ms": 600_000,
        })

        self.flow_mon   = FlowImbalanceMonitor(flow_cfg,   self.alert_engine)
        self.spread_mon = SpreadStressMonitor(spread_cfg,  self.alert_engine)
        self.qi_mon     = QueueImbalanceMonitor(queue_cfg, self.alert_engine)

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
            # 1) write raw tick to disk
            await self.file_sink.add(data)

            event = data.get("event", "ticker")

            # ------ trade: order‑flow imbalance ---------------------- #
            if event == "trade":
                await self.flow_mon.add(data)
                return  # no need to feed ticker logic

            # ------ depth: spread & queue monitors ------------------- #
            if event == "depth":
                await self.spread_mon.add(data)
                await self.qi_mon.add(data)
                return

            # ------ ticker: price‑move alerts ------------------------ #
            exch: str = data["exchange"]
            sym:  str = data["symbol"]
            ts:   int = int(data.get("timestamp", 0))

            try:
                price = float(data["price"])
            except (TypeError, ValueError):
                return  # malformed ticker payload

            # update last price cache
            self.store.update(exch, sym, price, ts)
            logger.debug("Stored %s %s @ %s", exch, sym, price)

            # evaluate alert thresholds configured in YAML
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
        """Send an e‑mail alert; retry up to MAX_RETRIES times."""
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
