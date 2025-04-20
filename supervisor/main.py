"""
Crypto‑Supervisor main entry point (async‑first version).

Changes vs. original
--------------------
* uses `asyncio.run()` instead of manual loop plumbing
* pluggable log‑level via --log‑level
* safer dynamic import with importlib + snake→Camel helper
* graceful shutdown that closes websockets & cancels tasks
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import logging
import signal
from typing import List, Type

from supervisor.config import load_config
from supervisor.processors.handler import TickHandler

logger = logging.getLogger("supervisor.main")


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def snake_to_camel(name: str) -> str:
    """binance → Binance, coinbase_pro → CoinbasePro, etc."""
    return "".join(part.capitalize() for part in name.split("_"))


def import_connector(exchange_name: str) -> Type:
    """
    Dynamically import a connector module and return its connector class.

    1. If the module exposes `CONNECTOR_CLASS`, use that.
    2. Otherwise fall back to `<CamelCase>Connector`.
    """
    module_path = f"supervisor.connectors.{exchange_name}"
    mod = importlib.import_module(module_path)

    if hasattr(mod, "CONNECTOR_CLASS"):
        return getattr(mod, "CONNECTOR_CLASS")

    class_name = f"{snake_to_camel(exchange_name)}Connector"
    if hasattr(mod, class_name):
        return getattr(mod, class_name)

    raise ImportError(f"{module_path} is missing a connector class")


def create_connectors(cfg: dict, handler: TickHandler):
    connectors = []
    for exch_name, exch_cfg in cfg["exchanges"].items():
        try:
            ConnectorCls = import_connector(exch_name)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not import connector %s: %s", exch_name, exc)
            continue

        connectors.append(
            ConnectorCls(
                symbols=exch_cfg["symbols"],
                message_handler=handler.handle_tick,
                reconnect_delay=exch_cfg.get("reconnect_delay", 5),
                streams=exch_cfg.get("streams"),          # ← add this line
            )
        )

    return connectors


# --------------------------------------------------------------------------- #
# service runner                                                              #
# --------------------------------------------------------------------------- #
async def run_service(args):
    cfg = load_config(args.config)
    handler = TickHandler(cfg)

    connectors = create_connectors(cfg, handler)
    if not connectors:
        logger.error("No valid connectors available – aborting.")
        return

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown():
        logger.info("Shutdown signal received – stopping connectors …")
        for c in connectors:
            loop.create_task(c.stop())
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _shutdown)

    # kick off connector tasks
    tasks = [
        asyncio.create_task(c.start(), name=f"{c.__class__.__name__}.start")
        for c in connectors
    ]
    logger.info("Crypto Supervisor started with %d connector(s).", len(tasks))

    # wait until shutdown requested
    await stop_event.wait()

    # cancel outstanding tasks
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Crypto Supervisor stopped.")


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Crypto Supervisor Service")
    p.add_argument(
        "-c",
        "--config",
        default="config/exchanges.yaml",
        help="Path to exchanges configuration YAML",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Console log level (default INFO)",
    )
    return p.parse_args(argv)


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main():
    args = parse_args()
    setup_logging(args.log_level)
    try:
        asyncio.run(run_service(args))
    except KeyboardInterrupt:
        # already handled by signal handler on Unix; this is for Windows
        pass


if __name__ == "__main__":
    main()
