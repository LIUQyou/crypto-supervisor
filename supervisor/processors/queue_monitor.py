import time, logging
from typing import Dict

from .rolling_window import RollingWindowMixin

logger = logging.getLogger(__name__)

class QueueImbalanceMonitor(RollingWindowMixin):
    """Level‑1 queue imbalance sustained over window."""

    def __init__(self, cfg: Dict, alert_engine):
        super().__init__(cfg["window_ms"])
        self.th           = cfg["threshold"]      # e.g. 0.7
        self.cooldown_ms  = cfg["cooldown_ms"]
        self.last_alert: Dict[str, int] = {}
        self._alert_engine = alert_engine

    async def add(self, depth: Dict):
        sym  = depth["symbol"]
        ts   = depth["timestamp"]
        bidq = depth["best_bid"][1] if depth["best_bid"] else 0
        askq = depth["best_ask"][1] if depth["best_ask"] else 0
        if bidq + askq == 0:
            return
        q = (bidq - askq) / (bidq + askq)        # [-1, +1]
        dq = self._push(sym, ts, q)

        if dq and all(abs(v) >= self.th for _, v in dq):
            now = int(time.time() * 1000)
            if now - self.last_alert.get(sym, 0) < self.cooldown_ms:
                return
            self.last_alert[sym] = now
            side = "buy" if dq[-1][1] > 0 else "sell"
            pct = f"{self.th:.0%}"
            alert = {
                "subject": f"{sym} persistent {side}-side queue imbalance ≥{pct}",
                "message": f"Level-1 queue imbalance exceeded {pct} for {self.window_ms/1000:.0f}s."
            }
            logger.info("QI alert queued: %s", alert["subject"])
            await self._alert_engine.send(alert)
