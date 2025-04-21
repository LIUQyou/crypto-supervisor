import time, logging
from typing import Dict
from prometheus_client import Counter   # pip install prometheus-client

from .rolling_window import RollingWindowMixin

logger = logging.getLogger(__name__)

class FlowImbalanceMonitor(RollingWindowMixin):
    """
    Computes signed notional imbalance over a rolling window and
    raises an alert when |imbalance| > threshold for the first time
    after the cooldown.
    """

    def __init__(self, cfg: Dict, alert_engine):
        super().__init__(cfg["window_ms"])
        self.th            = cfg["threshold"]
        self.min_notional  = cfg["min_notional"]
        self.cooldown_ms   = cfg["cooldown_ms"]
        self.last_alert: Dict[str, int] = {}
        self._alert_engine = alert_engine
        self._metric       = Counter(  # Prometheus
            "flow_alert_total", "flow imbalance alerts", ["symbol"]
        )

    async def add(self, trade: Dict):
        sym = trade["symbol"]
        ts  = trade["timestamp"]
        notion = float(trade["qty"]) * float(trade["price"])
        notion = notion if trade["side"] == "buy" else -notion

        dq = self._push(sym, ts, notion)
        gross = sum(abs(v) for _, v in dq)
        if gross < self.min_notional:
            return
        imb = sum(v for _, v in dq) / gross         # [-1, +1]

        if abs(imb) < self.th:
            return

        now = int(time.time() * 1000)
        if now - self.last_alert.get(sym, 0) < self.cooldown_ms:
            return

        self.last_alert[sym] = now
        side = "buy" if imb > 0 else "sell"
        pct  = f"{imb:+.0%}"
        alert = {
            "subject": f"{sym} {side}-flow {pct} (window {self.window_ms/1000:.0f}s)",
            "message": f"Signed notional imbalance = {pct} during the last "
                       f"{self.window_ms/1000:.0f} seconds."
        }
        logger.info("Flow alert queued: %s", alert["subject"])
        self._metric.labels(sym).inc()
        await self._alert_engine.send(alert)
