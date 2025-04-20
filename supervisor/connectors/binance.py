"""
Multi‑stream WebSocket connector for Binance.

Now supports:
* 24h ticker    — <symbol>@ticker         (price, volume, etc.)
* Agg trades    — <symbol>@aggTrade       (side‑aware executions)
* Depth 100 ms  — <symbol>@depth@100ms    (best bid/ask updates)

Down‑stream messages all include:
  {
    "event": "ticker" | "trade" | "depth",
    "exchange": "binance",
    "symbol":   "BTC/USDT",
    … event‑specific extras …
  }
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Dict, List, Optional

from websockets import connect

from supervisor.connectors.base import BaseConnector

__all__ = ["BinanceConnector"]


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _norm(symbol: str) -> str:
    """`BTC/USDT` → `btcusdt`"""
    return symbol.replace("/", "").lower()


# --------------------------------------------------------------------------- #
# connector                                                                   #
# --------------------------------------------------------------------------- #
class BinanceConnector(BaseConnector):
    WS_URL = "wss://stream.binance.com:9443/ws"

    def __init__(
        self,
        symbols: List[str],
        message_handler,
        reconnect_delay: int = 5,
        streams: Optional[List[str]] = None,
    ):
        """
        Parameters
        ----------
        symbols : list[str]
            Trading pairs as in the YAML (`BTC/USDT`).
        message_handler : coroutine
            Callback receiving the normalised dict per event.
        reconnect_delay : int
            Base seconds for exponential back‑off.
        streams : list[str] | None
            Any of {"ticker", "aggTrade", "depth"}; defaults to ["ticker"].
        """
        streams = streams or ["ticker"]

        self.symbols_cfg = symbols
        self.streams_cfg = set(streams)
        self.message_handler = message_handler
        self.base_delay = reconnect_delay

        # raw (btcusdt)  -> cfg (BTC/USDT)
        self._raw_to_cfg: Dict[str, str] = {_norm(s): s for s in symbols}

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
        self.logger.info("Connecting to Binance at %s", self.WS_URL)
        async with connect(self.WS_URL, max_queue=None) as ws:
            self._ws = ws
            await self._subscribe(ws)
            await self._router(ws)

    async def _subscribe(self, ws):
        params = []
        for sym in self.symbols_cfg:
            norm = _norm(sym)
            if "ticker" in self.streams_cfg:
                params.append(f"{norm}@ticker")
            if "aggTrade" in self.streams_cfg:
                params.append(f"{norm}@aggTrade")
            if "depth" in self.streams_cfg:
                params.append(f"{norm}@depth@100ms")

        sub_msg = {"method": "SUBSCRIBE", "params": params, "id": 1}
        await ws.send(json.dumps(sub_msg))
        self.logger.info("Subscribed (%s): %s", ", ".join(self.streams_cfg), ", ".join(self.symbols_cfg))

    # ------------- message demux ---------------------------------------- #
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
        self._ws = None  # drop ref

    # ---------- individual handlers ------------------------------------- #
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
            "qty":   float(msg["q"]),
            "side":  "sell" if msg["m"] else "buy",   # maker was seller?
            "timestamp": int(msg["E"]),
        }
        await self.message_handler(out)

    async def _handle_depth(self, msg):
        cfg_sym = self._raw_to_cfg.get(msg.get("s", "").lower())
        if not cfg_sym:
            return

        bids = [(float(p), float(q)) for p, q in msg["b"]]
        asks = [(float(p), float(q)) for p, q in msg["a"]]

        # mid‑price is a convenient scalar for pipelines that expect "price"
        if bids and asks:
            mid_price = (bids[0][0] + asks[0][0]) / 2
        elif bids:            # only bids updated – use best bid
            mid_price = bids[0][0]
        elif asks:            # only asks updated – use best ask
            mid_price = asks[0][0]
        else:
            # message with empty levels; skip it
            return

        out = {
            "event": "depth",
            "exchange": "binance",
            "symbol": cfg_sym,
            "timestamp": int(msg["E"]),
            "best_bid": bids[0] if bids else None,
            "best_ask": asks[0] if asks else None,
            "price": mid_price,          # so existing TickHandler still works
            "bids": bids,                # full lists in case you need them
            "asks": asks,
        }
        await self.message_handler(out)
