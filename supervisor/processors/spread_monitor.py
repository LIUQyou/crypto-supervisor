import time, logging
from typing import Dict
from prometheus_client import Counter

from .rolling_window import RollingWindowMixin

logger = logging.getLogger(__name__)

class SpreadStressMonitor(RollingWindowMixin):
    """Alerts when bid/ask spread widens beyond threshold‐bps in window."""

    def __init__(self, cfg: Dict, alert_engine):
        super().__init__(cfg["window_ms"])
        self.th_bps      = cfg["threshold_bps"]
        self.cooldown_ms = cfg["cooldown_ms"]
        self.last_alert: Dict[str, int] = {}
        self._alert_engine = alert_engine
        self._metric = Counter(
            "spread_alert_total", "spread‑widen alerts", ["symbol"]
        )

    async def add(self, depth: Dict):
        sym = depth["symbol"]
        ts  = depth["timestamp"]
        bid = depth["best_bid"][0] if depth["best_bid"] else None
        ask = depth["best_ask"][0] if depth["best_ask"] else None
        if bid is None or ask is None:
            return
        mid = (bid + ask) / 2
        spread_bps = 1e4 * (ask - bid) / mid

        dq = self._push(sym, ts, spread_bps)
        worst = max(v for _, v in dq)
        if worst < self.th_bps:
            return

        now = int(time.time() * 1000)
        if now - self.last_alert.get(sym, 0) < self.cooldown_ms:
            return

        self.last_alert[sym] = now
        alert = {
            "subject": f"{sym} spread {worst:.0f} bp (liquidity stress)",
            "message": f"Spread peaked at ≈{worst:.1f} bp during the last "
                       f"{self.window_ms/1000:.0f} seconds."
        }
        logger.info("Spread alert queued: %s", alert["subject"])
        self._metric.labels(sym).inc()
        await self._alert_engine.send(alert)
