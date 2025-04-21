"""
Multi‑stream WebSocket connector for Binance — *v2*
-------------------------------------------------
Adds **order‑book snapshot + delta maintenance** so that every
`depth` event now carries *true* best‑bid / best‑ask and (optionally)
full bid/ask ladders.  Sequence‑gap handling and automatic re‑sync are
implemented according to Binance docs.

New keyword arguments
---------------------
* **depth_levels** — (int, default ``20``) maximum price levels to
  include in the outgoing ``bids`` / ``asks`` list.  Set to 0 to omit.
* **snapshot_limit** — (int, default ``1000``) depth rows requested
  from the REST snapshot (`/api/v3/depth`).  Binance allows 100, 500 or
  1000.

Behavioural changes
-------------------
* A REST snapshot is downloaded *before* the WebSocket subscription
  starts.  If the socket closes or the sequence becomes inconsistent, a
  fresh snapshot is pulled and the connector resubscribes automatically.
* ``_handle_depth`` no longer assumes that the first bid/ask in the
  delta is best‑bid/ask.  It maintains an in‑memory ``dict[price] →
  size`` and computes best quotes on the fly.

Everything else (ticker / aggTrade payload schema) remains unchanged, so
existing processors (FlowAccumulator, SpreadMonitor, etc.) work without
modification.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Dict, List, Optional, Tuple

import aiohttp
from websockets import connect

from supervisor.connectors.base import BaseConnector

__all__ = ["BinanceConnector"]

BINANCE_REST = "https://api.binance.com"


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #

def _norm(symbol: str) -> str:
    """Normalise pair name: ``BTC/USDT`` → ``btcusdt``."""
    return symbol.replace("/", "").lower()


def _top_of_book(book_side: Dict[float, float], side: str) -> Optional[Tuple[float, float]]:
    """Return *(price, qty)* of best bid/ask, or *None* if side is empty."""
    if not book_side:
        return None
    return (max if side == "bid" else min)(book_side.items(), key=lambda x: x[0])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# connector                                                                   #
# --------------------------------------------------------------------------- #
class BinanceConnector(BaseConnector):
    WS_URL = "wss://stream.binance.com:9443/ws"

    # --------------------------------------------------------------------- #
    # init                                                                 #
    # --------------------------------------------------------------------- #
    def __init__(
        self,
        symbols: List[str],
        message_handler,
        reconnect_delay: int = 5,
        streams: Optional[List[str]] = None,
        *,
        depth_levels: int = 20,
        snapshot_limit: int = 1000,
    ):
        """Create a connector for one or more *symbols* (e.g. ``BTC/USDT``).

        Parameters
        ----------
        message_handler : coroutine
            Callback receiving the normalised dict per event.
        streams : list[str] | None
            Any of {"ticker", "aggTrade", "depth"}.  Defaults to ["ticker"].
        depth_levels : int
            How many levels (per side) to include in outgoing ``bids`` / ``asks``.
            ``0`` ⇒ send only best‑bid / best‑ask.
        snapshot_limit : int
            Row count for REST snapshot; Binance supports 100 / 500 / 1000.
        """
        streams = streams or ["ticker"]

        self.symbols_cfg = symbols
        self.streams_cfg = set(streams)
        self.message_handler = message_handler
        self.base_delay = reconnect_delay
        self.depth_levels = depth_levels
        self.snapshot_limit = snapshot_limit

        # raw (btcusdt)  -> cfg (BTC/USDT)
        self._raw_to_cfg: Dict[str, str] = {_norm(s): s for s in symbols}

        # per‑symbol order book state
        self._bids: Dict[str, Dict[float, float]] = {s: {} for s in symbols}
        self._asks: Dict[str, Dict[float, float]] = {s: {} for s in symbols}
        self._last_id: Dict[str, int] = {s: 0 for s in symbols}

        self.logger = logging.getLogger(self.__class__.__name__)
        self._stop_event = asyncio.Event()
        self._ws = None  # type: Optional[connect]

    # --------------------------------------------------------------------- #
    # life‑cycle                                                            #
    # --------------------------------------------------------------------- #
    async def start(self):
        attempt = 0
        while not self._stop_event.is_set():
            if attempt:
                backoff = min(self.base_delay * 2 ** attempt, 60)
                jitter = random.uniform(0, backoff * 0.2)
                self.logger.info("Reconnecting in %.1fs …", backoff + jitter)
                await asyncio.sleep(backoff + jitter)

            try:
                await self._run_once()
                attempt = 0
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                self.logger.error("Binance connection error: %s", exc)
                attempt += 1

    async def stop(self):
        self._stop_event.set()
        if self._ws and not self._ws.closed:
            await self._ws.close(code=1000, reason="client shutdown")

    # --------------------------------------------------------------------- #
    # internal                                                              #
    # --------------------------------------------------------------------- #
    async def _run_once(self):
        # 1. download snapshots *before* opening the socket
        await self._download_all_snapshots()

        # 2. open WebSocket connection
        self.logger.info("Connecting to Binance at %s", self.WS_URL)
        async with connect(self.WS_URL, max_queue=None) as ws:
            self._ws = ws
            await self._subscribe(ws)
            await self._router(ws)

    # ------------------------- REST snapshots --------------------------- #
    async def _download_all_snapshots(self):
        async with aiohttp.ClientSession() as sess:
            for cfg_sym in self.symbols_cfg:
                norm = _norm(cfg_sym)
                url = f"{BINANCE_REST}/api/v3/depth?symbol={norm.upper()}&limit={self.snapshot_limit}"
                async with sess.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Snapshot error {resp.status} for {cfg_sym}")
                    snap = await resp.json()
                self._init_book_from_snapshot(cfg_sym, snap)
                self.logger.info("Snapshot loaded for %s (lastUpdateId=%s)", cfg_sym, snap["lastUpdateId"])

    def _init_book_from_snapshot(self, cfg_sym: str, snap: Dict):
        bids = {float(p): float(q) for p, q in snap["bids"] if float(q) > 0}
        asks = {float(p): float(q) for p, q in snap["asks"] if float(q) > 0}
        self._bids[cfg_sym] = bids
        self._asks[cfg_sym] = asks
        self._last_id[cfg_sym] = int(snap["lastUpdateId"])

    # ------------------------- subscription ---------------------------- #
    async def _subscribe(self, ws):
        params = []
        for sym in self.symbols_cfg:
            norm = _norm(sym)
            if "ticker" in self.streams_cfg:
                params.append(f"{norm}@ticker")
            if "aggTrade" in self.streams_cfg:
                params.append(f"{norm}@aggTrade")
            if "depth" in self.streams_cfg:
                # full depth deltas (not 5/10/20) every 100 ms
                params.append(f"{norm}@depth@100ms")

        sub_msg = {"method": "SUBSCRIBE", "params": params, "id": 1}
        await ws.send(json.dumps(sub_msg))
        self.logger.info("Subscribed (%s): %s", ", ".join(self.streams_cfg), ", ".join(self.symbols_cfg))

    # ------------------------- message router -------------------------- #
    async def _router(self, ws):
        async for raw in ws:
            if self._stop_event.is_set():
                break
            try:
                msg = json.loads(raw)
                ev = msg.get("e")
                if ev == "24hrTicker":
                    await self._handle_ticker(msg)
                elif ev == "aggTrade":
                    await self._handle_trade(msg)
                elif ev == "depthUpdate":
                    await self._handle_depth(msg)
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("Bad Binance payload: %s (%s)", raw, exc)
        self._ws = None  # drop ref so start() can reconnect

    # -------------------- individual handlers ------------------------- #
    async def _handle_ticker(self, msg):
        cfg_sym = self._raw_to_cfg.get(msg.get("s", "").lower())
        if not cfg_sym:
            return

        out = {
            "event": "ticker",
            "exchange": "binance",
            "symbol": cfg_sym,
            "price": float(msg["c"]),
            "timestamp": int(msg["E"]),
            "volume": float(msg["v"]),
            "quote_volume": float(msg["q"]),
            "best_bid": float(msg["b"]),
            "best_ask": float(msg["a"]),
        }
        await self.message_handler(out)

    async def _handle_trade(self, msg):
        cfg_sym = self._raw_to_cfg.get(msg.get("s", "").lower())
        if not cfg_sym:
            return

        out = {
            "event": "trade",
            "exchange": "binance",
            "symbol": cfg_sym,
            "price": float(msg["p"]),
            "qty": float(msg["q"]),
            "side": "sell" if msg["m"] else "buy",  # maker was seller?
            "timestamp": int(msg["E"]),
        }
        await self.message_handler(out)

    # -------------------------- depth --------------------------------- #
    async def _handle_depth(self, msg):
        cfg_sym = self._raw_to_cfg.get(msg.get("s", "").lower())
        if not cfg_sym:
            return

        first_id: int = msg["U"]  # start of update id range
        last_id: int = msg["u"]   # end of range (inclusive)
        cur_last = self._last_id[cfg_sym]

        # 1. discard outdated update
        if last_id <= cur_last:
            return

        # 2. gap check — if missing packets → resync
        if not (first_id <= cur_last + 1 <= last_id):
            self.logger.warning("Seq gap for %s (have %d, got %d-%d) — resync", cfg_sym, cur_last, first_id, last_id)
            await self._resync_symbol(cfg_sym)
            return

        # 3. apply delta to local book
        self._apply_delta(cfg_sym, msg)
        self._last_id[cfg_sym] = last_id

        bids = self._bids[cfg_sym]
        asks = self._asks[cfg_sym]
        best_bid = _top_of_book(bids, "bid")
        best_ask = _top_of_book(asks, "ask")

        if not (best_bid and best_ask):  # incomplete book → ignore
            return

        mid_price = (best_bid[0] + best_ask[0]) / 2.0

        out = {
            "event": "depth",
            "exchange": "binance",
            "symbol": cfg_sym,
            "timestamp": int(msg["E"]),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "price": mid_price,  # compatibility shim
        }
        if self.depth_levels:
            # sort depths and cut to requested levels
            out["bids"] = sorted(bids.items(), key=lambda x: -x[0])[: self.depth_levels]
            out["asks"] = sorted(asks.items(), key=lambda x: x[0])[: self.depth_levels]
        await self.message_handler(out)

    def _apply_delta(self, cfg_sym: str, msg: Dict):
        bids = self._bids[cfg_sym]
        asks = self._asks[cfg_sym]
        for p_str, q_str in msg.get("b", []):
            price, qty = float(p_str), float(q_str)
            if qty == 0:
                bids.pop(price, None)
            else:
                bids[price] = qty
        for p_str, q_str in msg.get("a", []):
            price, qty = float(p_str), float(q_str)
            if qty == 0:
                asks.pop(price, None)
            else:
                asks[price] = qty

    async def _resync_symbol(self, cfg_sym: str):
        self.logger.info("Resyncing order book for %s", cfg_sym)
        async with aiohttp.ClientSession() as sess:
            norm = _norm(cfg_sym)
            url = f"{BINANCE_REST}/api/v3/depth?symbol={norm.upper()}&limit={self.snapshot_limit}"
            async with sess.get(url, timeout=10) as resp:
                snap = await resp.json()
        self._init_book_from_snapshot(cfg_sym, snap)
