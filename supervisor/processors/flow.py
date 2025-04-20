"""
FlowAccumulator
===============

Tracks aggressive buy/sell volume from Binance **aggTrade** events,
rolls them into 1‑minute buckets, and fires an alert when the
imbalance in the last *N* buckets exceeds a configurable threshold.

Config fragment expected (see exchanges.yaml):

alerts:
  flow:
    threshold:      0.6        # 60 % imbalance
    bucket_ms:      60000      # 1‑min buckets
    window_buckets: 3          # roll 3 buckets = 3 min window
    min_notional:   100000     # ignore low volume
    cooldown_ms:    600000     # 10 min cool‑down
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, List

import logging

logger = logging.getLogger(__name__)


class FlowAccumulator:
    """Aggregate buy/sell flow and raise imbalance alerts."""

    def __init__(self, cfg: Dict, alert_engine):
        # config
        self.bucket_ms: int = cfg["bucket_ms"]
        self.window: int = cfg["window_buckets"]
        self.th: float = cfg["threshold"]
        self.min_notional: float = cfg["min_notional"]
        self.cooldown_ms: int = cfg["cooldown_ms"]

        # state
        self.buckets: Dict[str, Deque[Dict]] = defaultdict(
            lambda: deque(maxlen=self.window)
        )  # symbol -> deque of recent buckets
        self.last_alert_ts: Dict[str, int] = {}  # sym -> last alert time ms

        self._alert_engine = alert_engine

    # ------------------------------------------------------------------ #
    # public API                                                         #
    # ------------------------------------------------------------------ #
    async def add(self, trade: Dict):
        """
        Consume a normalised **trade** event:

        {
          "event": "trade",
          "exchange": "binance",
          "symbol":  "BTC/USDT",
          "price":   float,
          "qty":     float,
          "side":    "buy" | "sell",
          "timestamp": int (ms)
        }
        """
        symbol: str = trade["symbol"]
        ts: int = trade["timestamp"]
        side: str = trade["side"]
        qty: float = float(trade["qty"])
        notional: float = qty * float(trade["price"])

        bucket_id = ts // self.bucket_ms
        dq = self.buckets[symbol]

        # create new bucket if needed
        if not dq or dq[-1]["bucket"] != bucket_id:
            dq.append(
                {
                    "bucket": bucket_id,
                    "buy_qty": 0.0,
                    "sell_qty": 0.0,
                    "notional": 0.0,
                }
            )

        cur = dq[-1]
        if side == "buy":
            cur["buy_qty"] += qty
        else:
            cur["sell_qty"] += qty
        cur["notional"] += notional

        # if deque is full (window complete) -> evaluate
        if len(dq) == self.window:
            await self._check_window(symbol, list(dq))

    # ------------------------------------------------------------------ #
    # internal                                                           #
    # ------------------------------------------------------------------ #
    async def _check_window(self, symbol: str, window_buckets: List[Dict]):
        buy_qty = sum(b["buy_qty"] for b in window_buckets)
        sell_qty = sum(b["sell_qty"] for b in window_buckets)
        tot_notional = sum(b["notional"] for b in window_buckets)

        if tot_notional < self.min_notional:
            return

        imbalance = (buy_qty - sell_qty) / (buy_qty + sell_qty)
        if abs(imbalance) < self.th:
            return

        now = int(time.time() * 1000)
        if now - self.last_alert_ts.get(symbol, 0) < self.cooldown_ms:
            return  # still in cooldown

        self.last_alert_ts[symbol] = now
        side = "buy" if imbalance > 0 else "sell"
        pct = f"{imbalance:+.0%}"

        alert = {
            "subject": f"{symbol} {side}-flow {pct} over last "
                       f"{self.window * self.bucket_ms / 60000:.0f} min",
            "message": (
                f"{symbol} volume imbalance: {pct} ({buy_qty=:.2f}, "
                f"{sell_qty=:.2f}) notional≈${tot_notional:,.0f} "
                f"in the last {self.window} bucket(s)."
            ),
        }

        logger.info("Flow alert queued: %s", alert["subject"])
        await self._alert_engine.send(alert)
