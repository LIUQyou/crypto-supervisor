"""scripts/tick_analyzer.py
================================
CLI helper to load FileSink shards, resample, and plot **(now with
save‑to‑file support)**.

Usage examples
--------------
1. 1‑second trade bars, export data + save chart:

```
python -m scripts.tick_analyzer \
       --base-dir ticks          \
       --symbol BTC_USDT         \
       --event trade             \
       --from "2025-04-21 10:00" \
       --to   "2025-04-21 10:10" \
       --bar 1s                  \
       --out btc_1s.parquet      \
       --save btc_chart.png
```

2. Just plot mid‑price of depth events interactively:

```
python -m scripts.tick_analyzer \
       --symbol BTC_USDT --event depth \
       --from "2025-04-21 10:00" --to "2025-04-21 10:05" --plot
```

Dependencies
~~~~~~~~~~~~
* `polars>=0.20`, `pyarrow`
* `matplotlib` only when `--plot` or `--save` is requested
"""
from __future__ import annotations

import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import List

import polars as pl

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_dt(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str).astimezone(timezone.utc)


def _bucket_files(base: Path, sym: str, event: str, start: datetime, end: datetime) -> List[Path]:
    files: List[Path] = []
    date_range = pl.date_range(start.date(), end.date(), interval="1d", eager=True)
    for day in date_range:
        folder = base / sym / event / day.strftime("%Y/%m/%d")
        if folder.is_dir():
            files.extend(p for p in folder.iterdir() if p.suffix in (".parquet", ".csv"))
    return sorted(files)


def _read_concat(files: List[Path]) -> pl.DataFrame:
    if not files:
        raise FileNotFoundError("No files found for the given window.")
    parts = [pl.read_parquet(f) if f.suffix == ".parquet" else pl.read_csv(f) for f in files]
    return pl.concat(parts, how="vertical_relaxed")


def _resample_trade(df: pl.DataFrame, every: str) -> pl.DataFrame:
    df = df.with_columns(pl.col("timestamp").cast(pl.Int64))
    bars = (
        df.groupby_dynamic(index_column="timestamp", every=every, closed="left")
          .agg([
              pl.col("price").first().alias("open"),
              pl.col("price").max().alias("high"),
              pl.col("price").min().alias("low"),
              pl.col("price").last().alias("close"),
              pl.col("qty").sum().alias("volume"),
              (pl.col("price") * pl.col("qty")).sum().alias("notional"),
          ])
          .with_columns((pl.col("notional") / pl.col("volume")).alias("vwap"))
          .drop("notional")
          .sort("timestamp")
    )
    return bars

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description="Tick data analyzer & plotter")
    p.add_argument("--base-dir", default="ticks", help="root of FileSink output")
    p.add_argument("--symbol", required=True, help="symbol name, e.g. BTC_USDT")
    p.add_argument("--event", required=True, choices=["trade", "ticker", "depth"], help="event type to load")
    p.add_argument("--from", dest="start", required=True, help="UTC start time ISO e.g. '2025-04-21 10:00'")
    p.add_argument("--to", dest="end", required=True, help="UTC end time ISO")
    p.add_argument("--bar", default="1s", help="bar size for trade resample (1s,5s,1min…)")
    p.add_argument("--out", help="export DataFrame to parquet/csv path")
    p.add_argument("--plot", action="store_true", help="show interactive plot")
    p.add_argument("--save", help="save plot to image file (png/pdf/svg)")
    args = p.parse_args(argv)

    base   = Path(args.base_dir)
    start  = _parse_dt(args.start)
    end    = _parse_dt(args.end)

    files  = _bucket_files(base, args.symbol, args.event, start, end)
    df     = _read_concat(files)

    if args.event == "trade":
        df = _resample_trade(df, args.bar)
    df = df.filter((pl.col("timestamp") >= int(start.timestamp()*1000)) &
                   (pl.col("timestamp") <= int(end.timestamp()*1000)))

    # ---------------- export ----------------
    if args.out:
        if args.out.endswith(".parquet"):
            df.write_parquet(args.out)
        else:
            df.write_csv(args.out)

    # ---------------- plot ------------------
    if args.plot or args.save:
        import matplotlib.pyplot as plt
        ts = df["timestamp"].to_numpy() / 1000
        if args.event == "trade":
            plt.plot(ts, df["close"], label="close")
        else:
            col = "price" if "price" in df.columns else df.columns[1]
            plt.plot(ts, df[col], label=col)
        plt.legend(); plt.title(f"{args.symbol} {args.event}")
        plt.xlabel("unix time (s)")
        if args.save:
            plt.savefig(args.save, dpi=150, bbox_inches="tight")
        if args.plot:
            plt.show()

if __name__ == "__main__":
    main()
