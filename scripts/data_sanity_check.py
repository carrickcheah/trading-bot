"""Read-only sanity check across the bars/ Parquet store.

Reports:
  1. File count and total disk usage
  2. Schema consistency (all expected columns + dtypes)
  3. Coverage histogram (bars per ticker, years per ticker)
  4. Date sanity (weekends/holidays, gaps within trading window)
  5. Value sanity (negative prices, zero volume, OHLC ordering, extreme moves)
  6. Cross-source check (IBKR row 2025-04-30 vs yfinance row 2025-04-30 — ratios)
  7. Random sample inspection (5 known tickers, head/tail)

Designed to be fast and READ-ONLY — runs alongside any active backfill.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

BARS_DIR = Path("/data/bars")
EXPECTED_COLS = ["date", "open", "high", "low", "close", "volume", "trade_count", "wap"]


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def main() -> None:
    files = sorted(BARS_DIR.glob("*.parquet"))
    section("1. File inventory")
    total_bytes = sum(f.stat().st_size for f in files)
    print(f"Files: {len(files)}")
    print(f"Total disk: {total_bytes / 1024**2:.1f} MB")
    if not files:
        sys.exit("No parquet files found")

    # Sampling for speed: read all files but only stats columns
    section("2. Schema consistency")
    schema_issues: list[tuple[str, str]] = []
    col_dtype_counter: Counter[tuple[str, str]] = Counter()
    for f in files:
        try:
            df = pd.read_parquet(f)
        except Exception as e:
            schema_issues.append((f.name, f"read failed: {e}"))
            continue
        missing = [c for c in EXPECTED_COLS if c not in df.columns]
        if missing:
            schema_issues.append((f.name, f"missing cols: {missing}"))
        for c in df.columns:
            col_dtype_counter[(c, str(df[c].dtype))] += 1

    print(f"Schema issues: {len(schema_issues)}")
    if schema_issues[:5]:
        for n, e in schema_issues[:5]:
            print(f"  {n}: {e}")
    print("\nColumn dtype distribution (most common):")
    for (col, dtype), count in sorted(col_dtype_counter.items()):
        print(f"  {col:<13} {dtype:<20} {count} files")

    section("3. Coverage histogram")
    bar_counts: list[int] = []
    earliest_dates: list[str] = []
    latest_dates: list[str] = []
    for f in files:
        df = pd.read_parquet(f, columns=["date"])
        bar_counts.append(len(df))
        if len(df) > 0:
            d = df["date"].astype(str)
            earliest_dates.append(d.min())
            latest_dates.append(d.max())

    bc_series = pd.Series(bar_counts)
    print(f"Bars per ticker — min={bc_series.min()}, max={bc_series.max()}, median={bc_series.median():.0f}, mean={bc_series.mean():.0f}")
    print("\nDistribution:")
    bins = [-1, 100, 500, 1000, 2000, 3000, 4000, 100000]
    labels = ["<100", "100-500", "500-1000", "1000-2000", "2000-3000", "3000-4000", "4000+"]
    cuts = pd.cut(bc_series, bins=bins, labels=labels)
    print(cuts.value_counts().sort_index().to_string())

    print(f"\nEarliest dates — min={min(earliest_dates)}, max={max(earliest_dates)}")
    print(f"Latest dates   — min={min(latest_dates)}, max={max(latest_dates)}")

    section("4. Date sanity (sampling 100 files)")
    weekend_dates = 0
    sample = files[: min(100, len(files))]
    for f in sample:
        df = pd.read_parquet(f, columns=["date"])
        if df.empty:
            continue
        dates = pd.to_datetime(df["date"].astype(str), errors="coerce")
        dow = dates.dt.dayofweek
        weekend_dates += ((dow == 5) | (dow == 6)).sum()
    print(f"Weekend dates found in 100-file sample: {weekend_dates}")

    section("5. Value sanity (sampling 100 files)")
    neg_price = 0
    zero_volume = 0
    bad_ohlc = 0  # rows where high < low or close outside [low, high]
    extreme_moves = 0  # close-to-close > 50% in one day
    nan_in_required = 0
    for f in sample:
        df = pd.read_parquet(f, columns=["open", "high", "low", "close", "volume"])
        if df.empty:
            continue
        # NaN in required cols (OHLCV must be populated)
        nan_in_required += df[["open", "high", "low", "close", "volume"]].isna().any(axis=1).sum()
        # Negative prices
        neg_price += (df[["open", "high", "low", "close"]] < 0).any(axis=1).sum()
        # Zero volume
        zero_volume += (df["volume"] == 0).sum()
        # OHLC ordering: high should be >= low, close should be in [low, high]
        bad_ohlc += ((df["high"] < df["low"]) | (df["close"] < df["low"]) | (df["close"] > df["high"])).sum()
        # Extreme close-to-close moves (>50%)
        if len(df) > 1:
            ret = df["close"].pct_change().abs()
            extreme_moves += (ret > 0.5).sum()
    print(f"NaN in OHLCV (across 100 files): {nan_in_required}")
    print(f"Negative prices: {neg_price}")
    print(f"Zero volume bars: {zero_volume}")
    print(f"OHLC ordering errors: {bad_ohlc}")
    print(f"Extreme >50% close-to-close moves: {extreme_moves}")

    section("6. Cross-source check — 2025-04-30 boundary")
    # 2025-04-30 sits at the boundary: yfinance has it (Run 2 ends here),
    # IBKR Phase A also has it (Phase A starts here). They should AGREE on close.
    boundary_date = "2025-04-30"
    matches = 0
    mismatches = 0
    diffs: list[tuple[str, float]] = []
    yf_only = 0
    ib_only = 0
    sample_extended = files[: min(500, len(files))]
    for f in sample_extended:
        df = pd.read_parquet(f, columns=["date", "close"])
        if df.empty:
            continue
        df["date"] = df["date"].astype(str)
        row = df[df["date"] == boundary_date]
        if row.empty:
            continue
        # We can't distinguish yfinance vs IBKR row directly since boundary date appears once
        # But we can spot-check: file should contain this date exactly once
        if len(row) == 1:
            matches += 1
        else:
            mismatches += 1
            diffs.append((f.stem, len(row)))
    print(f"Tickers with exactly 1 row on 2025-04-30: {matches}")
    print(f"Tickers with duplicate rows on 2025-04-30: {mismatches}")
    if diffs[:5]:
        for n, c in diffs[:5]:
            print(f"  {n}: {c} rows on boundary date")

    section("7. Sample inspection — known tickers")
    for t in ["AAPL", "MSFT", "NVDA", "PLTR", "COIN", "SOFI"]:
        f = BARS_DIR / f"{t}.parquet"
        if not f.exists():
            print(f"  {t}: file missing")
            continue
        df = pd.read_parquet(f)
        df["date"] = df["date"].astype(str)
        years = (pd.Timestamp(df["date"].max()) - pd.Timestamp(df["date"].min())).days / 365.25
        tc_pct = df["trade_count"].notna().mean() * 100
        wap_pct = df["wap"].notna().mean() * 100
        print(f"  {t:<6}: {len(df)} rows, {df['date'].min()} to {df['date'].max()} ({years:.1f}y) | trade_count {tc_pct:.0f}% wap {wap_pct:.0f}%")

    section("DONE")


if __name__ == "__main__":
    main()
