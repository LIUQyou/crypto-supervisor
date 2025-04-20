"""
SpreadMonitor
=============

Observes best bid/ask updates from depth events and alerts when the
spread widens beyond a threshold (in basis‑points) for a sustained
window.

alerts:
  spread:
    threshold_bps: 30      # widen > 30 bp
    window_ms:     10000   # look at last 10 s of samples
    cooldown_ms:   600000  # 10 min silence after each alert
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, Tuple

import logging

logger = logging.getLogger(__name__)


class SpreadMonitor:
    """Tracks bid‑ask spread and fires liquidity‑stress alerts."""

    def __init__(self, cfg: Dict, alert_engine):
        self.th_bps: float = cfg["threshold_bps"]
        self.window_ms: int = cfg["window_ms"]
        self.cooldown_ms: int = cfg["cooldown_ms"]

        # symbol -> deque[(ts, spread_bps)]
        self.history: Dict[str, Deque[Tuple[int, float]]] = {}
        # symbol -> last alert ts
        self.last_alert: Dict[str, int] = {}

        self._alert_engine = alert_engine

    # ------------------------------------------------------------------ #
    # public API                                                         #
    # ------------------------------------------------------------------ #
    async def add(self, depth: Dict):
        """
        Accept a depth event produced by BinanceConnector._handle_depth():

        {
          "event": "depth",
          "symbol": "BTC/USDT",
          "timestamp": 123456789,
          "best_bid": (price, qty) | None,
          "best_ask": (price, qty) | None,
          ...
        }
        """
        sym = depth["symbol"]
        ts = depth["timestamp"]

        bid_price = depth["best_bid"][0] if depth["best_bid"] else None
        ask_price = depth["best_ask"][0] if depth["best_ask"] else None
        if bid_price is None or ask_price is None:
            # book snapshot incomplete – ignore
            return

        mid = (bid_price + ask_price) / 2
        spread_bps = 10_000 * (ask_price - bid_price) / mid

        dq = self.history.setdefault(sym, deque())
        dq.append((ts, spread_bps))

        # drop samples outside the rolling window
        cutoff = ts - self.window_ms
        while dq and dq[0][0] < cutoff:
            dq.popleft()

        worst = max(s for _, s in dq)
        if worst < self.th_bps:
            return

        now = int(time.time() * 1000)
        if now - self.last_alert.get(sym, 0) < self.cooldown_ms:
            return  # still cooling down

        # raise alert
        self.last_alert[sym] = now
        alert = {
            "subject": f"{sym} spread {worst:.0f} bp (liquidity stress)",
            "message": (
                f"Best bid/ask spread widened to approximately {worst:.1f} bp "
                f"within the last {self.window_ms/1000:.0f} seconds."
            ),
        }
        logger.info("Spread alert queued: %s", alert["subject"])
        await self._alert_engine.send(alert)
