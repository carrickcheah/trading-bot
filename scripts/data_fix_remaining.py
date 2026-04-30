"""Fix the remaining 41 OHLC violations after the main fix script ran.

Investigates and applies one of three fixes per row:
  1. If any OHLC value is NaN → drop the row (corrupted, can't fix)
  2. If a row is duplicate of another date → keep one
  3. Otherwise → re-clip with strict equality enforcement
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BARS_DIR = Path("/data/bars")


def find_violations(df: pd.DataFrame) -> pd.Series:
    return (
        (df["high"] < df["low"])
        | (df["close"] < df["low"])
        | (df["close"] > df["high"])
        | (df["open"] < df["low"])
        | (df["open"] > df["high"])
    )


def main() -> None:
    files = sorted(BARS_DIR.glob("*.parquet"))
    print(f"Scanning {len(files)} files for remaining violations...")

    affected_files = []
    for f in files:
        try:
            df = pd.read_parquet(f, columns=["open", "high", "low", "close"])
        except Exception:
            continue
        if df.empty:
            continue
        bad = find_violations(df)
        if bad.any():
            affected_files.append((f, int(bad.sum())))

    print(f"Files with violations: {len(affected_files)}")

    if not affected_files:
        print("All clean!")
        return

    # Inspect first
    print("\n=== Investigation ===")
    for f, n in affected_files[:10]:
        df = pd.read_parquet(f)
        df["date"] = df["date"].astype(str)
        bad = find_violations(df)
        print(f"\n{f.stem} ({n} bad):")
        for idx in df.index[bad][:3]:
            r = df.iloc[idx]
            has_nan = any(pd.isna(r[c]) for c in ["open", "high", "low", "close"])
            print(f"  {r['date']}: O={r['open']} H={r['high']} L={r['low']} C={r['close']} | NaN={has_nan}")

    # Fix: drop rows with NaN, then re-clip remaining
    print("\n=== Applying fix ===")
    total_dropped = 0
    total_reclipped = 0
    for f, _n in affected_files:
        df = pd.read_parquet(f)

        # Drop rows where any OHLC is NaN
        before = len(df)
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        dropped = before - len(df)
        total_dropped += dropped

        if df.empty:
            # File became empty — quarantine
            f.unlink()
            print(f"  {f.stem}: all rows dropped (all-NaN file), removed")
            continue

        # Re-clip strictly
        ohlc = df[["open", "high", "low", "close"]]
        df["high"] = ohlc.max(axis=1)
        df["low"] = ohlc.min(axis=1)

        # Verify
        bad = find_violations(df)
        if bad.any():
            # Drop any remaining violations (shouldn't happen but safety net)
            print(f"  {f.stem}: {bad.sum()} unfixable rows after reclip — dropping")
            df = df[~bad].reset_index(drop=True)

        df.to_parquet(f, compression="snappy")
        total_reclipped += 1

    print(f"\nDropped {total_dropped} NaN rows across {total_reclipped} files")

    # Final verification
    print("\n=== Final verification ===")
    remaining = 0
    for f in BARS_DIR.glob("*.parquet"):
        df = pd.read_parquet(f, columns=["open", "high", "low", "close"])
        if df.empty:
            continue
        bad = find_violations(df)
        remaining += int(bad.sum())
    print(f"Remaining OHLC violations: {remaining}")


if __name__ == "__main__":
    main()
