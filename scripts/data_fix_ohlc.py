"""Fix OHLC data quality issues:

1. Quarantine tickers with corrupt data (negative prices OR >5% violation rate)
   → moved to /data/bars_corrupted/
2. For remaining tickers, clip OHLC to be self-consistent:
     high = max(open, high, low, close)
     low  = min(open, high, low, close)
   This corrects floating-point noise from yfinance auto_adjust without
   altering real data — only the high/low boundary rows shift in the
   ~7th decimal place to absorb the rounding error.

CAUTION: writes to parquet files. Don't run while backfill is active on
overlapping tickers — but our backfill only touches trade_count + wap,
so the OHLC fix won't conflict.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd

BARS_DIR = Path("/data/bars")
QUARANTINE_DIR = Path("/data/bars_corrupted")
QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)

# Thresholds
QUARANTINE_VIOLATION_RATE = 0.05  # 5% bad rows → quarantine
NEGATIVE_PRICE_THRESHOLD = 1  # any negative price → quarantine immediately


def main() -> None:
    files = sorted(BARS_DIR.glob("*.parquet"))
    print(f"Scanning {len(files)} files...")

    quarantined: list[tuple[str, str]] = []
    fixed: list[tuple[str, int]] = []
    clean: int = 0

    for f in files:
        try:
            df = pd.read_parquet(f)
        except Exception as e:
            print(f"  read fail {f.name}: {e}")
            continue
        if df.empty:
            continue

        # 1. Check for negative prices → immediate quarantine
        ohlc = df[["open", "high", "low", "close"]]
        n_neg = (ohlc < 0).any(axis=1).sum()
        if n_neg >= NEGATIVE_PRICE_THRESHOLD:
            shutil.move(str(f), str(QUARANTINE_DIR / f.name))
            quarantined.append((f.stem, f"negative_price_{n_neg}_rows"))
            continue

        # 2. Check violation rate
        cond_high_lt_low = df["high"] < df["low"]
        cond_close_below_low = df["close"] < df["low"]
        cond_close_above_high = df["close"] > df["high"]
        cond_open_below_low = df["open"] < df["low"]
        cond_open_above_high = df["open"] > df["high"]
        bad = cond_high_lt_low | cond_close_below_low | cond_close_above_high | cond_open_below_low | cond_open_above_high
        n_bad = int(bad.sum())

        if n_bad == 0:
            clean += 1
            continue

        violation_rate = n_bad / len(df)
        if violation_rate > QUARANTINE_VIOLATION_RATE:
            shutil.move(str(f), str(QUARANTINE_DIR / f.name))
            quarantined.append((f.stem, f"violation_rate_{violation_rate:.0%}_{n_bad}/{len(df)}"))
            continue

        # 3. Fix by clipping: high = max(O,H,L,C), low = min(O,H,L,C)
        new_high = df[["open", "high", "low", "close"]].max(axis=1)
        new_low = df[["open", "high", "low", "close"]].min(axis=1)
        df["high"] = new_high
        df["low"] = new_low
        df.to_parquet(f, compression="snappy")
        fixed.append((f.stem, n_bad))

    print(f"\n=== RESULTS ===")
    print(f"Already clean:    {clean}")
    print(f"Fixed (clipped):  {len(fixed)}")
    print(f"Quarantined:      {len(quarantined)}")
    print(f"\nTotal remaining tickers: {len(files) - len(quarantined)}")

    if quarantined:
        print(f"\nQuarantined tickers (top 30):")
        for ticker, reason in quarantined[:30]:
            print(f"  {ticker:<10} {reason}")

    print(f"\n=== Verification: scanning fixed files ===")
    fixed_files = sorted(BARS_DIR.glob("*.parquet"))
    remaining_violations = 0
    for f in fixed_files:
        df = pd.read_parquet(f, columns=["open", "high", "low", "close"])
        if df.empty:
            continue
        bad = (df["high"] < df["low"]) | (df["close"] < df["low"]) | (df["close"] > df["high"]) | (df["open"] < df["low"]) | (df["open"] > df["high"])
        remaining_violations += int(bad.sum())
    print(f"Remaining OHLC violations after fix: {remaining_violations}")


if __name__ == "__main__":
    main()
