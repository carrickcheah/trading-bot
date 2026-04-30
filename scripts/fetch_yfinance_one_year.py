"""Fetch ONE specific year of daily bars for all tickers via yfinance, append to existing Parquet.

Yahoo Finance fallback when IBKR gateway is unavailable. Same target schema:
    date, open, high, low, close, volume, trade_count, wap
trade_count and wap are NaN for yfinance data (only IBKR provides those).

Env vars:
  TARGET_START   YYYY-MM-DD (e.g. 2024-04-30)
  TARGET_END     YYYY-MM-DD (e.g. 2025-04-29)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

BARS_DIR = Path(os.environ.get("BARS_DIR", "/data/bars"))
LOG = Path(os.environ.get("BARS_LOG", "/data/logs/fetch_yfinance.log"))
TARGET_START = os.environ.get("TARGET_START", "2024-04-30")
TARGET_END = os.environ.get("TARGET_END", "2025-04-29")


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")


def yf_to_df(yf_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance output to our 8-column schema."""
    if yf_df is None or yf_df.empty:
        return pd.DataFrame()
    if isinstance(yf_df.columns, pd.MultiIndex):
        yf_df.columns = yf_df.columns.get_level_values(0)
    yf_df = yf_df.reset_index()
    return pd.DataFrame({
        "date": yf_df["Date"].dt.strftime("%Y-%m-%d"),
        "open": yf_df["Open"].astype(float),
        "high": yf_df["High"].astype(float),
        "low": yf_df["Low"].astype(float),
        "close": yf_df["Close"].astype(float),
        "volume": yf_df["Volume"].astype(float),
        "trade_count": pd.NA,
        "wap": pd.NA,
    })


def main() -> None:
    log(f"=== yfinance fetch: {TARGET_START} to {TARGET_END} ===")

    tickers = sorted(p.stem for p in BARS_DIR.glob("*.parquet"))
    log(f"Tickers to attempt: {len(tickers)}")

    appended = 0
    no_data = 0
    skipped = 0
    failed: list[tuple[str, str]] = []
    start = time.time()

    for i, ticker in enumerate(tickers, 1):
        out = BARS_DIR / f"{ticker}.parquet"
        try:
            existing = pd.read_parquet(out)
        except Exception:
            existing = pd.DataFrame()

        # Skip if we already have ≥200 bars in the target window
        if not existing.empty:
            existing_dates = pd.to_datetime(existing["date"])
            ts_start = pd.Timestamp(TARGET_START)
            ts_end = pd.Timestamp(TARGET_END)
            count = ((existing_dates >= ts_start) & (existing_dates <= ts_end)).sum()
            if count >= 200:
                skipped += 1
                continue

        try:
            yf_df = yf.download(
                ticker,
                start=TARGET_START,
                end=TARGET_END,
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            new_year = yf_to_df(yf_df)
            if new_year.empty or len(new_year) < 30:
                no_data += 1
                continue
            # Normalize date column to string (IBKR stored datetime.date, we stored str)
            if not existing.empty:
                existing = existing.copy()
                existing["date"] = existing["date"].astype(str)
            combined = (
                pd.concat([existing, new_year], ignore_index=True)
                .drop_duplicates(subset="date")
                .sort_values("date")
                .reset_index(drop=True)
            )
            combined.to_parquet(out, compression="snappy")
            appended += 1
            if i % 100 == 0:
                rate = i / max(time.time() - start, 1e-3)
                eta = (len(tickers) - i) / max(rate, 1e-3)
                log(f"[{i}/{len(tickers)}] appended={appended} no_data={no_data} skipped={skipped} | rate={rate:.2f}/s ETA={eta/60:.1f}min")
        except Exception as e:
            failed.append((ticker, str(e)[:80]))

    elapsed = time.time() - start
    log("\n=== DONE ===")
    log(f"Appended: {appended}, Skipped: {skipped}, NoData: {no_data}, Failed: {len(failed)}")
    log(f"Elapsed: {elapsed:.1f}s ({elapsed/60:.1f}min)")
    if failed:
        with (BARS_DIR.parent / f"yf_fetch_{TARGET_START}_failures.json").open("w") as f:
            json.dump(failed, f, indent=2)


if __name__ == "__main__":
    main()
