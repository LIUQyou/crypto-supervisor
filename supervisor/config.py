"""
YAML configuration loader & validator for Crypto Supervisor.

New in this version
-------------------
* merges in sane defaults for the alerts section
* validates that alert thresholds are numeric
* raises early, clear exceptions instead of logging‑and‑continuing
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# defaults                                                                    #
# --------------------------------------------------------------------------- #
DEFAULT_ALERTS: Dict[str, Any] = {
    "thresholds": {
        "pct_24h": 0.05,
        "window_24h_ms": 24 * 3600 * 1000,
        "pct_short": 0.02,
        "window_short_ms": 3600 * 1000,
    },
    "email": {},
}


def _recursive_merge(base: dict, override: dict) -> dict:
    """Non‑destructive deep merge (override wins)."""
    merged = base.copy()
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merged[k] = _recursive_merge(base[k], v)
        else:
            merged[k] = v
    return merged


# --------------------------------------------------------------------------- #
# public API                                                                  #
# --------------------------------------------------------------------------- #
def load_config(path: str | Path) -> dict:
    """
    Read YAML file, apply defaults, and validate structure.

    :param path: path to config YAML (str or Path)
    :returns: fully‑populated config dict
    :raises FileNotFoundError, ValueError
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf‑8") as fh:
        cfg = yaml.safe_load(fh) or {}

    # ───── exchanges section ─────────────────────────────────────────────── #
    if "exchanges" not in cfg or not isinstance(cfg["exchanges"], dict):
        raise ValueError("Config must contain an 'exchanges' mapping")

    for name, params in cfg["exchanges"].items():
        if "symbols" not in params or not isinstance(params["symbols"], list):
            raise ValueError(f"Exchange '{name}' must define a list of 'symbols'")
        params.setdefault("reconnect_delay", 5)

    # ───── alerts section (merge defaults) ───────────────────────────────── #
    cfg["alerts"] = _recursive_merge(DEFAULT_ALERTS, cfg.get("alerts", {}))

    # basic sanity check on numeric threshold fields
    th = cfg["alerts"]["thresholds"]
    for key in ("pct_24h", "pct_short"):
        if not isinstance(th[key], (int, float)) or th[key] <= 0:
            raise ValueError(f"alerts.thresholds.{key} must be a positive number")
    for key in ("window_24h_ms", "window_short_ms"):
        if not isinstance(th[key], int) or th[key] <= 0:
            raise ValueError(f"alerts.thresholds.{key} must be a positive integer")

    return cfg
