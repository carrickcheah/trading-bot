"""Daily update: fetch latest bars from Yahoo Finance and append to Parquet store.

Idempotent — safe to re-run. For each ticker:
  1. Read existing parquet, find the last saved date
  2. Fetch 5 trading days from yfinance (covers weekends/holidays)
  3. Append only rows newer than the last saved date
  4. Write back to parquet

Run after US market close (≥4:30pm ET):
    uv run python scripts/fetch_yfinance_daily.py

Env vars:
    BARS_DIR        Default /data/bars
    BATCH_SIZE      Default 200 tickers per yfinance call
    LOOKBACK_DAYS   Default 5 (covers a long weekend gap)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

BARS_DIR = Path(os.environ.get("BARS_DIR", "/data/bars"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "200"))
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "5"))


def list_tickers() -> list[str]:
    return sorted(f.stem for f in BARS_DIR.glob("*.parquet"))


def last_saved_date(parquet_path: Path) -> str | None:
    try:
        df = pd.read_parquet(parquet_path, columns=["date"])
        return None if df.empty else str(df["date"].max())
    except Exception:
        return None


def yf_to_rows(yf_df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if yf_df is None or yf_df.empty:
        return pd.DataFrame()
    if isinstance(yf_df.columns, pd.MultiIndex):
        try:
            yf_df = yf_df.xs(ticker, axis=1, level=1)
        except KeyError:
            return pd.DataFrame()
    yf_df = yf_df.reset_index().dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    if yf_df.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "date": yf_df["Date"].dt.strftime("%Y-%m-%d"),
            "open": yf_df["Open"].astype(float),
            "high": yf_df["High"].astype(float),
            "low": yf_df["Low"].astype(float),
            "close": yf_df["Close"].astype(float),
            "volume": yf_df["Volume"].astype(float),
            "trade_count": pd.NA,
            "wap": pd.NA,
        }
    )


def append_new_rows(parquet_path: Path, new_rows: pd.DataFrame) -> int:
    """Returns number of rows actually appended."""
    if new_rows.empty:
        return 0
    if parquet_path.exists():
        existing = pd.read_parquet(parquet_path)
        existing["date"] = existing["date"].astype(str)
        new_rows["date"] = new_rows["date"].astype(str)
        last = existing["date"].max()
        fresh = new_rows[new_rows["date"] > last]
        if fresh.empty:
            return 0
        # Align columns to existing schema
        for col in existing.columns:
            if col not in fresh.columns:
                fresh[col] = pd.NA
        fresh = fresh[existing.columns]
        merged = pd.concat([existing, fresh], ignore_index=True)
    else:
        fresh = new_rows
        merged = new_rows

    merged.to_parquet(parquet_path, index=False)
    return len(fresh)


def main() -> None:
    if not BARS_DIR.exists():
        sys.exit(f"BARS_DIR does not exist: {BARS_DIR}")

    tickers = list_tickers()
    if not tickers:
        sys.exit(f"No parquet files in {BARS_DIR}")

    end_date = datetime.utcnow().date() + timedelta(days=1)
    start_date = end_date - timedelta(days=LOOKBACK_DAYS + 2)
    print(f"Updating {len(tickers):,} tickers from {start_date} to {end_date}")
    t0 = time.time()

    appended_total = 0
    skipped = 0
    failed = 0

    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        try:
            df = yf.download(
                batch,
                start=start_date.isoformat(),
                end=end_date.isoformat(),
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception as e:
            failed += len(batch)
            print(f"  batch {i}: download failed — {e}")
            continue

        for ticker in batch:
            try:
                if isinstance(df.columns, pd.MultiIndex) and ticker in df.columns.levels[0]:
                    rows = yf_to_rows(df[ticker], ticker)
                else:
                    rows = yf_to_rows(df, ticker)
                if rows.empty:
                    skipped += 1
                    continue
                n = append_new_rows(BARS_DIR / f"{ticker}.parquet", rows)
                appended_total += n
                if n == 0:
                    skipped += 1
            except Exception as e:
                failed += 1
                print(f"  {ticker}: {e}")

        elapsed = time.time() - t0
        print(
            f"  [{i + len(batch):>5,} / {len(tickers):,}]  "
            f"appended={appended_total:,}  skipped={skipped:,}  failed={failed:,}  "
            f"({elapsed:.0f}s)"
        )

    print(
        f"\nDone in {time.time() - t0:.1f}s — "
        f"appended {appended_total} rows across {len(tickers)} tickers "
        f"(skipped {skipped} no-new-data, {failed} failed)"
    )


if __name__ == "__main__":
    main()
