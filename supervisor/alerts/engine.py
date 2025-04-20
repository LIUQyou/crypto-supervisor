"""
AlertEngine v2  –  Threshold checks with cool‑down & improved lookup logic.
"""

from __future__ import annotations

import bisect
import logging
import time
from collections import deque
from typing import Deque, Dict, List, Tuple

from supervisor.alerts.email import EmailSender

PRICE = float
TS = int


class AlertEngine:
    """
    Detects large price moves and dispatches email alerts.

    Config dictionary keys
    ----------------------
    thresholds:
        pct_24h           : float  –  e.g. 0.05  (5 %)
        window_24h_ms     : int    –  default 24h
        pct_short         : float  –  e.g. 0.02  (2 %)
        window_short_ms   : int    –  default 1h
        cooldown_ms       : int    –  suppress repeat alerts for this period (default = window_short_ms)

    email:
        … passed straight through to EmailSender
    """

    def __init__(self, config: Dict):
        th = config.get("thresholds", {})

        self.pct_24h = th.get("pct_24h", 0.05)
        self.w24 = th.get("window_24h_ms", 24 * 3600 * 1000)

        self.pct_short = th.get("pct_short", 0.02)
        self.w_short = th.get("window_short_ms", 3600 * 1000)

        self.cooldown = th.get("cooldown_ms", self.w_short)

        # history: (exchange, symbol) -> deque[(ts, price)]
        self.history: Dict[Tuple[str, str], Deque[Tuple[TS, PRICE]]] = {}

        # last‑alert map: (exchange, symbol, "24h"/"short") -> last_ts
        self.last_alert: Dict[Tuple[str, str, str], TS] = {}

        self.email_sender = EmailSender(config.get("email", {}))
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------ #
    # public interface                                                   #
    # ------------------------------------------------------------------ #
    def check(self, exchange: str, symbol: str, price: float, ts: TS | None):
        """
        Feed a new tick; return list of alert dicts ready for dispatch.
        """
        now = ts or int(time.time() * 1000)
        key = (exchange, symbol)

        dq = self.history.setdefault(key, deque())
        dq.append((now, price))

        # prune anything older than the bigger window
        oldest = now - max(self.w24, self.w_short)
        while dq and dq[0][0] < oldest:
            dq.popleft()

        alerts: List[Dict[str, str]] = []

        # helper for de‑duplication
        def _should_alert(tag: Tuple[str, str, str]):
            last = self.last_alert.get(tag, 0)
            return now - last >= self.cooldown

        # ---- 24 h window ------------------------------------------------ #
        ref_price_24h = self._price_at_or_before(dq, now - self.w24)
        if ref_price_24h:
            pct = (price - ref_price_24h) / ref_price_24h
            tag = key + ("24h",)
            if abs(pct) >= self.pct_24h and _should_alert(tag):
                alerts.append(
                    {
                        "subject": f"{symbol} moved {pct:+.2%} over 24 h",
                        "message": (
                            f"{exchange}:{symbol} price is {price:.8g}, "
                            f"{pct:+.2%} versus 24 h ago ({ref_price_24h:.8g})."
                        ),
                    }
                )
                self.last_alert[tag] = now

        # ---- short window ---------------------------------------------- #
        ref_price_short = self._price_at_or_before(dq, now - self.w_short)
        if ref_price_short:
            pct_s = (price - ref_price_short) / ref_price_short
            tag = key + ("short",)
            if abs(pct_s) >= self.pct_short and _should_alert(tag):
                mins = self.w_short / 1000 / 60
                alerts.append(
                    {
                        "subject": f"{symbol} moved {pct_s:+.2%} in {mins:.0f} min",
                        "message": (
                            f"{exchange}:{symbol} price is {price:.8g}, "
                            f"{pct_s:+.2%} within the last {mins:.0f} minutes "
                            f"(was {ref_price_short:.8g})."
                        ),
                    }
                )
                self.last_alert[tag] = now

        return alerts

    async def send(self, alert: Dict[str, str]):
        """
        Fire the given alert email; logs failures for retry.
        """
        ok = await self.email_sender.send(alert["subject"], alert["message"])
        if not ok:
            self.logger.error("Email dispatch failed → %s", alert["subject"])

    # ------------------------------------------------------------------ #
    # helpers                                                            #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _price_at_or_before(
        dq: Deque[Tuple[TS, PRICE]], cutoff: TS
    ) -> float | None:
        """
        Return the price whose timestamp is the latest that is *≤ cutoff*.

        Uses bisect on a list of timestamps extracted from the deque for O(log n).
        """
        if not dq or dq[0][0] > cutoff:
            return None

        ts_list = [ts for ts, _ in dq]
        idx = bisect.bisect_right(ts_list, cutoff) - 1
        if idx >= 0:
            return dq[idx][1]
        return None
