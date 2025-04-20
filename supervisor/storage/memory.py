"""
In‑memory price store (lock‑free version).

Notes
-----
* All accesses occur on the asyncio event loop’s single OS thread,
  so Python’s GIL already serialises byte‑code execution.
* Each public method performs only a handful of atomic dict operations;
  these are inherently thread‑safe with respect to the GIL.
"""

import time
from typing import Dict, Optional


class MemoryStore:
    """
    Simple in‑memory storage for the latest tick data.
    """

    # exchange -> symbol -> {"price": float, "timestamp": int}
    _store: Dict[str, Dict[str, Dict[str, float | int]]]

    def __init__(self):
        self._store = {}

    # ------------------------------------------------------------------ #
    # public API                                                         #
    # ------------------------------------------------------------------ #
    def update(
        self,
        exchange: str,
        symbol: str,
        price: float,
        timestamp: Optional[int] = None,
    ) -> None:
        """
        Record the latest price for *symbol* on *exchange*.

        Parameters
        ----------
        exchange : str
        symbol   : str
        price    : float
        timestamp: int | None   – epoch‑ms; if None, use current time
        """
        ts = timestamp or int(time.time() * 1000)

        ex_store = self._store.setdefault(exchange, {})
        ex_store[symbol] = {"price": price, "timestamp": ts}

    def get_latest(self, exchange: str, symbol: str):
        """
        Retrieve most recent tick for (*exchange*, *symbol*).

        Returns
        -------
        dict with keys {"price", "timestamp"} or None if absent.
        """
        return self._store.get(exchange, {}).get(symbol)

    def get_all(self):
        """
        Return a *shallow* snapshot of the current store.
        """
        return {ex: syms.copy() for ex, syms in self._store.items()}
