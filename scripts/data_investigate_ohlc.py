"""Find every OHLC ordering violation across all parquet files.

Reports tickers, dates, and exact violations. Read-only.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

BARS_DIR = Path("/data/bars")


def main() -> None:
    files = sorted(BARS_DIR.glob("*.parquet"))
    print(f"Scanning {len(files)} files...")

    total_bars = 0
    total_violations = 0
    violation_types: Counter[str] = Counter()
    affected_tickers: list[tuple[str, int]] = []  # (ticker, count)
    samples: list[tuple[str, str, dict]] = []  # (ticker, date, row)

    for f in files:
        try:
            df = pd.read_parquet(f, columns=["date", "open", "high", "low", "close"])
        except Exception:
            continue
        if df.empty:
            continue
        total_bars += len(df)
        df["date"] = df["date"].astype(str)

        # Conditions:
        cond_high_lt_low = df["high"] < df["low"]
        cond_close_below_low = df["close"] < df["low"]
        cond_close_above_high = df["close"] > df["high"]
        cond_open_below_low = df["open"] < df["low"]
        cond_open_above_high = df["open"] > df["high"]

        bad = cond_high_lt_low | cond_close_below_low | cond_close_above_high | cond_open_below_low | cond_open_above_high
        n_bad = int(bad.sum())
        if n_bad == 0:
            continue
        total_violations += n_bad
        affected_tickers.append((f.stem, n_bad))

        # Categorize
        violation_types["high<low"] += int(cond_high_lt_low.sum())
        violation_types["close<low"] += int(cond_close_below_low.sum())
        violation_types["close>high"] += int(cond_close_above_high.sum())
        violation_types["open<low"] += int(cond_open_below_low.sum())
        violation_types["open>high"] += int(cond_open_above_high.sum())

        # Save up to 5 sample rows
        if len(samples) < 30:
            for idx in df.index[bad][:3]:
                samples.append((
                    f.stem,
                    df.at[idx, "date"],
                    {
                        "open": float(df.at[idx, "open"]),
                        "high": float(df.at[idx, "high"]),
                        "low": float(df.at[idx, "low"]),
                        "close": float(df.at[idx, "close"]),
                    },
                ))

    print(f"\nScanned {total_bars:,} bars total")
    print(f"OHLC violations: {total_violations:,}")
    print(f"Tickers affected: {len(affected_tickers)} / {len(files)}")
    print(f"\nViolation types:")
    for kind, count in violation_types.most_common():
        print(f"  {kind:<14} {count:,}")

    print(f"\nWorst-affected tickers (top 20):")
    affected_tickers.sort(key=lambda x: x[1], reverse=True)
    for ticker, n in affected_tickers[:20]:
        print(f"  {ticker:<10} {n} bad rows")

    print(f"\nSample violations (showing 30):")
    for ticker, date, row in samples[:30]:
        print(f"  {ticker} on {date}: O={row['open']:.4f} H={row['high']:.4f} L={row['low']:.4f} C={row['close']:.4f}")


if __name__ == "__main__":
    main()
