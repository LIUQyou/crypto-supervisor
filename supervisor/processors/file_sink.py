"""supervisor.processors.file_sink
=================================
Parquet/CSV sink that **rotates files by a configurable interval** –
1 minute, 5 minutes, 1 hour… – so you can control how granularly raw
ticks are stored for later analysis & back‑testing.

Usage (plug into *TickHandler*):
-------------------------------
```python
self.sink = FileSink(
    base_dir="data",         # root directory for all symbols
    fmt="parquet",           # or "csv"
    rotate_minutes=5,         # 1,5,60 …
    max_rows=50_000,          # flush earlier if buffer huge
)
...
await self.sink.add(data)     # inside handle_tick()
```

Resulting files take the form:
```
<base_dir>/<symbol>/<event>/<yyyy>/<mm>/<dd>/<event>_<yyyy‑mm‑dd>_<HH‑MM>.parquet
# with HH‑MM being *floored* to the rotate interval boundary
```
Example for a 5‑minute rotation written at 11:07 UTC:
```
BTC_USDT/trade/2025/04/21/trade_2025‑04‑21_11‑05.parquet
```

Implementation notes
--------------------
* **Thread‑safe / async‑safe**: uses asyncio.Lock per bucket.
* **Flush conditions**: 1) interval crossed; or 2) buffered rows >=
  *max_rows*.
* **Parquet** uses Zstandard compression; CSV uses UTF‑8.
* Relies on *pyarrow* when writing Parquet.

Feel free to extend with S3 upload, auto‑gzip, etc.
"""
from __future__ import annotations

import asyncio
import os
import csv
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List

import logging

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # allow CSV‑only usage without pyarrow
    pa = None  # type: ignore
    pq = None  # type: ignore

logger = logging.getLogger(__name__)

__all__ = ["FileSink"]


def _bucket_start(ts_ms: int, rotate_minutes: int) -> datetime:
    """Return the UTC datetime floor‑bucket for *ts_ms*."""
    minutes = (ts_ms // 60000)  # integer minutes since epoch
    bucket = minutes - (minutes % rotate_minutes)
    return datetime.fromtimestamp(bucket * 60, tz=timezone.utc)


class FileSink:
    """Buffered file writer with time‑based rotation."""

    def __init__(
        self,
        base_dir: str = "data",
        *,
        fmt: str = "parquet",  # or "csv"
        rotate_minutes: int = 60,
        max_rows: int = 50_000,
        compression: str = "zstd",
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.fmt = fmt.lower()
        if self.fmt not in {"parquet", "csv"}:
            raise ValueError("fmt must be 'parquet' or 'csv'")
        self.rotate = rotate_minutes
        self.max_rows = max_rows
        self.compression = compression

        # bucket_key -> list[dict]
        self.buffers: Dict[str, List[dict]] = defaultdict(list)
        # bucket_key -> asyncio.Lock
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ------------------------------------------------------------------ #
    async def add(self, tick: Dict):
        """Add a *normalised* tick/event dict coming from TickHandler."""
        ts = int(tick.get("timestamp") or 0)
        if ts == 0:
            logger.debug("tick without timestamp skipped")
            return
        sym = tick["symbol"].replace("/", "_")
        ev = tick["event"]

        bucket_dt = _bucket_start(ts, self.rotate)  # floored datetime
        bucket_label = bucket_dt.strftime("%Y-%m-%d_%H-%M")
        key = f"{sym}|{ev}|{bucket_label}"

        buf = self.buffers[key]
        buf.append(tick)

        if len(buf) >= self.max_rows:
            async with self._locks[key]:
                await self._flush(key)

    # ------------------------------------------------------------------ #
    async def _flush(self, key: str):
        """Write buffer *key* to disk and clear it."""
        rows = self.buffers.get(key)
        if not rows:
            return

        sym, ev, label = key.split("|")
        dt = datetime.strptime(label, "%Y-%m-%d_%H-%M")
        out_dir = self.base_dir / sym / ev / dt.strftime("%Y/%m/%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{ev}_{label}.{ 'parquet' if self.fmt=='parquet' else 'csv' }"

        if self.fmt == "parquet":
            if pa is None:
                raise RuntimeError("pyarrow not installed – cannot write Parquet")
            table = pa.Table.from_pylist(rows)
            pq.write_table(table, out_file, compression=self.compression)
        else:  # CSV
            fieldnames = sorted({k for r in rows for k in r.keys()})
            write_header = not out_file.exists()
            with open(out_file, "a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=fieldnames)
                if write_header:
                    w.writeheader()
                w.writerows(rows)

        logger.debug("Flushed %d rows → %s", len(rows), out_file)
        self.buffers.pop(key, None)

    # ------------------------------------------------------------------ #
    async def periodic_flush(self):
        """Call periodically (e.g., once per minute) to flush finished buckets."""
        now = datetime.now(tz=timezone.utc)
        threshold = now - timedelta(minutes=self.rotate)
        to_flush = [k for k in self.buffers if _is_bucket_older(k, threshold)]
        for k in to_flush:
            async with self._locks[k]:
                await self._flush(k)


def _is_bucket_older(key: str, threshold: datetime) -> bool:
    *_, label = key.split("|")
    dt = datetime.strptime(label, "%Y-%m-%d_%H-%M")
    return dt < threshold
