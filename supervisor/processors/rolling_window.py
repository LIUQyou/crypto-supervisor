from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

class RollingWindowMixin:
    """
    Helper that maintains a per‑symbol deque of (timestamp, value)
    clipped to a rolling time window (ms).
    """

    def __init__(self, window_ms: int):
        self.window_ms = window_ms
        self._hist: Dict[str, Deque[Tuple[int, float]]] = defaultdict(deque)

    def _push(self, sym: str, ts: int, val: float):
        dq = self._hist[sym]
        dq.append((ts, val))
        cut = ts - self.window_ms
        while dq and dq[0][0] < cut:
            dq.popleft()
        return dq
