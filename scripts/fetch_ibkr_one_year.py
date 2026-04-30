"""Fetch ONE specific year of daily bars for all tickers, append to existing Parquet.

Designed for incremental backfill: run once for each year going back.
  Run 1: TARGET_END_DATE=20250429  → fetches 2024-04-30 → 2025-04-29
  Run 2: TARGET_END_DATE=20240429  → fetches 2023-04-30 → 2024-04-29
  Run 3: TARGET_END_DATE=20230428  → fetches 2022-04-29 → 2023-04-28
  ...

Each run reads existing Parquet, fetches the new year, dedupes, sorts,
and overwrites. Idempotent — re-running same year is a no-op.

Env vars:
  TARGET_END_DATE   YYYYMMDD (default: yesterday)
  CLIENT_ID         default 12
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from ib_async import IB, Stock

BARS_DIR = Path(os.environ.get("BARS_DIR", "/data/bars"))
LOG = Path(os.environ.get("BARS_LOG", "/data/logs/fetch_one_year.log"))
TARGET_END = os.environ.get("TARGET_END_DATE", (datetime.now() - timedelta(days=1)).strftime("%Y%m%d"))
CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "12"))
HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
PORT = int(os.environ.get("IBKR_PORT", "4002"))


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")


def bars_to_df(bars) -> pd.DataFrame:
    return pd.DataFrame([{
        "date": b.date,
        "open": b.open,
        "high": b.high,
        "low": b.low,
        "close": b.close,
        "volume": b.volume,
        "trade_count": b.barCount,
        "wap": b.average,
    } for b in bars])


async def fetch_one_year(ib: IB, ticker: str, end_dt_str: str) -> pd.DataFrame | None:
    contract = Stock(ticker, "SMART", "USD")
    try:
        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime=f"{end_dt_str} 23:59:59 US/Eastern",
            durationStr="1 Y",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
    except Exception:
        return None
    if not bars:
        return None
    return bars_to_df(bars)


async def main() -> None:
    target_year_label = f"~{TARGET_END[:4]}"
    log(f"=== Fetching year ending {TARGET_END} (label: {target_year_label}) ===")

    tickers = sorted(p.stem for p in BARS_DIR.glob("*.parquet"))
    log(f"Tickers to attempt: {len(tickers)}")

    ib = IB()
    await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, timeout=15, readonly=True)
    log(f"Connected to IBKR (server v{ib.client.serverVersion()})")

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

        # Check whether we already have bars covering the target year-end date
        if not existing.empty:
            existing_dates = pd.to_datetime(existing["date"])
            target_dt = pd.Timestamp(TARGET_END[:4] + "-" + TARGET_END[4:6] + "-" + TARGET_END[6:])
            window_start = target_dt - pd.Timedelta(days=370)
            count_in_window = ((existing_dates >= window_start) & (existing_dates <= target_dt)).sum()
            if count_in_window >= 200:
                skipped += 1
                continue

        try:
            new_year = await fetch_one_year(ib, ticker, TARGET_END)
            if new_year is None or new_year.empty:
                no_data += 1
                continue
            combined = (
                pd.concat([existing, new_year], ignore_index=True)
                .drop_duplicates(subset="date")
                .sort_values("date")
                .reset_index(drop=True)
            )
            combined.to_parquet(out, compression="snappy")
            appended += 1
            if i % 50 == 0:
                rate = i / max(time.time() - start, 1e-3)
                eta = (len(tickers) - i) / max(rate, 1e-3)
                log(f"[{i}/{len(tickers)}] appended={appended} no_data={no_data} skipped={skipped} | rate={rate:.2f}/s ETA={eta/60:.1f}min")
        except Exception as e:
            failed.append((ticker, str(e)[:80]))

    elapsed = time.time() - start
    ib.disconnect()
    log("\n=== DONE ===")
    log(f"Appended: {appended}, Skipped (already had): {skipped}, NoData: {no_data}, Failed: {len(failed)}")
    log(f"Elapsed: {elapsed:.1f}s ({elapsed/60:.1f}min)")
    if failed:
        with (BARS_DIR.parent / f"fetch_{TARGET_END}_failures.json").open("w") as f:
            json.dump(failed, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
