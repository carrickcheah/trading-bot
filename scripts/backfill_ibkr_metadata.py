"""Backfill `trade_count` and `wap` columns in existing Parquet files using IBKR.

Most parquet files have OHLCV from yfinance for older years (2010-2025) but only
have `trade_count` and `wap` populated for the IBKR Phase A year (~2025-04-30 to
2026-04-29). This script fills in the missing metadata columns by re-fetching
those date ranges from IBKR via `reqHistoricalData` and matching by date.

Behavior:
  * Reads each parquet file fresh per ticker (does NOT cache; tolerates concurrent
    yfinance writers appending rows)
  * Only updates `trade_count` and `wap` — never touches OHLCV
  * Walks backward in 1-year chunks (IBKR's max for daily bars)
  * Resumable: skips tickers whose `trade_count` is already non-NaN for >= 95%
    of rows (already complete)
  * Per-request timeout ~10s — skip and continue on timeout
  * Handles "Error 162: HMDS query returned no data" gracefully — leaves NaN

Env vars:
  BARS_DIR        default: /data/bars
  BARS_LOG        default: /data/logs/backfill_metadata.log
  IBKR_HOST       default: 127.0.0.1
  IBKR_PORT       default: 4002
  IBKR_CLIENT_ID  default: 12
  REQ_TIMEOUT     per-request timeout seconds (default 12)
  MAX_CHUNKS      max number of 1-year chunks to walk back (default 20)
  COMPLETE_RATIO  skip ticker if non-NaN ratio >= this (default 0.95)
  START_AT        process tickers starting at this name (resume support, alphabetical)
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
LOG = Path(os.environ.get("BARS_LOG", "/data/logs/backfill_metadata.log"))
HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
PORT = int(os.environ.get("IBKR_PORT", "4002"))
CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "12"))
REQ_TIMEOUT = float(os.environ.get("REQ_TIMEOUT", "12"))
MAX_CHUNKS = int(os.environ.get("MAX_CHUNKS", "20"))
COMPLETE_RATIO = float(os.environ.get("COMPLETE_RATIO", "0.95"))
START_AT = os.environ.get("START_AT", "")


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")


def bars_to_meta_df(bars) -> pd.DataFrame:
    """Extract just date + trade_count + wap from IBKR bars."""
    rows = []
    for b in bars:
        # b.date is datetime.date; normalize to "YYYY-MM-DD" string to match parquet schema
        d = b.date.strftime("%Y-%m-%d") if hasattr(b.date, "strftime") else str(b.date)
        rows.append({"date": d, "trade_count": b.barCount, "wap": b.average})
    return pd.DataFrame(rows)


async def fetch_chunk(ib: IB, contract: Stock, end_dt: str) -> pd.DataFrame | None:
    """Fetch one 1-year chunk ending at end_dt. Returns None on no-data/timeout/error.

    end_dt is "" for "now" or "YYYYMMDD HH:MM:SS US/Eastern" format.
    """
    try:
        bars = await asyncio.wait_for(
            ib.reqHistoricalDataAsync(
                contract,
                endDateTime=end_dt,
                durationStr="1 Y",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            ),
            timeout=REQ_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None
    if not bars:
        return None
    return bars_to_meta_df(bars)


async def fetch_metadata_for_range(
    ib: IB, ticker: str, oldest_needed: pd.Timestamp, newest_needed: pd.Timestamp
) -> pd.DataFrame:
    """Walk backward in 1-year chunks from newest_needed to oldest_needed.

    Returns concatenated metadata frame (may be empty if IBKR has no data).
    """
    contract = Stock(ticker, "SMART", "USD")
    chunks: list[pd.DataFrame] = []
    # endDateTime starts at newest_needed + 1 day buffer
    cursor = newest_needed + pd.Timedelta(days=1)
    for _ in range(MAX_CHUNKS):
        end_str = cursor.strftime("%Y%m%d 23:59:59 US/Eastern")
        df = await fetch_chunk(ib, contract, end_str)
        if df is None or df.empty:
            break
        chunks.append(df)
        oldest_in_chunk = pd.to_datetime(df["date"].min())
        # Stop when we've covered the oldest needed date
        if oldest_in_chunk <= oldest_needed:
            break
        # Move cursor back to day before oldest in this chunk
        cursor = oldest_in_chunk - pd.Timedelta(days=1)
    if not chunks:
        return pd.DataFrame(columns=["date", "trade_count", "wap"])
    out = (
        pd.concat(chunks, ignore_index=True)
        .drop_duplicates(subset="date")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return out


def needs_backfill(df: pd.DataFrame) -> tuple[bool, pd.Timestamp | None, pd.Timestamp | None]:
    """Return (needs_work, oldest_nan_date, newest_nan_date).

    A ticker needs backfill if `trade_count` has any NaN rows. We only need to fetch
    the date range covering those NaN rows.
    """
    if df.empty or "trade_count" not in df.columns:
        return False, None, None
    mask = df["trade_count"].isna()
    if not mask.any():
        return False, None, None
    nan_dates = pd.to_datetime(df.loc[mask, "date"])
    return True, nan_dates.min(), nan_dates.max()


async def process_ticker(ib: IB, ticker: str, parquet_path: Path) -> tuple[str, int, int]:
    """Process one ticker. Returns (status, rows_filled, rows_still_nan).

    status: "complete" | "filled" | "no_data" | "no_work" | "error"
    """
    try:
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        return f"error:read:{str(e)[:40]}", 0, 0

    if df.empty:
        return "no_work", 0, 0

    # Quick completion check by ratio
    if "trade_count" in df.columns:
        non_nan = df["trade_count"].notna().sum()
        if len(df) > 0 and (non_nan / len(df)) >= COMPLETE_RATIO:
            return "complete", 0, 0

    needs, oldest, newest = needs_backfill(df)
    if not needs:
        return "no_work", 0, 0

    # Fetch metadata covering NaN range
    meta = await fetch_metadata_for_range(ib, ticker, oldest, newest)
    if meta.empty:
        # No IBKR data for that range -- leave NaN
        nan_count = int(df["trade_count"].isna().sum())
        return "no_data", 0, nan_count

    # Re-read parquet fresh in case yfinance appended rows during our fetch
    try:
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        return f"error:reread:{str(e)[:40]}", 0, 0

    # Normalize date to string for join
    df = df.copy()
    df["date"] = df["date"].astype(str)
    meta["date"] = meta["date"].astype(str)

    # Build a date->{trade_count, wap} lookup from IBKR
    meta_idx = meta.set_index("date")
    # Only fill rows where existing trade_count is NaN
    nan_mask = df["trade_count"].isna()
    target_dates = df.loc[nan_mask, "date"]
    matched_tc = target_dates.map(lambda d: meta_idx.loc[d, "trade_count"] if d in meta_idx.index else pd.NA)
    matched_wap = target_dates.map(lambda d: meta_idx.loc[d, "wap"] if d in meta_idx.index else pd.NA)

    fill_count = int(matched_tc.notna().sum())
    if fill_count == 0:
        nan_count = int(df["trade_count"].isna().sum())
        return "no_data", 0, nan_count

    # Promote columns to nullable / float dtypes that can hold NA + numeric mix.
    # `trade_count` is int64 in IBKR-only files but may need to coexist with NaN
    # rows that we couldn't fill. Use float64 (NaN-friendly) to stay schema-compatible
    # with yfinance writes that already use NaN.
    if df["trade_count"].dtype.kind in ("i", "u"):
        df["trade_count"] = df["trade_count"].astype("float64")
    if df["wap"].dtype.kind in ("i", "u"):
        df["wap"] = df["wap"].astype("float64")

    # Convert matched values to numeric, coercing <NA> -> NaN
    tc_vals = pd.to_numeric(matched_tc, errors="coerce").values
    wap_vals = pd.to_numeric(matched_wap, errors="coerce").values
    df.loc[nan_mask, "trade_count"] = tc_vals
    df.loc[nan_mask, "wap"] = wap_vals

    # Save back atomically (write to temp, rename)
    tmp_path = parquet_path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp_path, compression="snappy")
    os.replace(tmp_path, parquet_path)

    still_nan = int(df["trade_count"].isna().sum())
    return "filled", int(fill_count), still_nan


async def main() -> None:
    log(f"=== backfill_ibkr_metadata start ===")
    log(f"BARS_DIR={BARS_DIR} HOST={HOST}:{PORT} CLIENT_ID={CLIENT_ID} TIMEOUT={REQ_TIMEOUT}s")

    files = sorted(BARS_DIR.glob("*.parquet"))
    if START_AT:
        files = [f for f in files if f.stem >= START_AT]
    log(f"Tickers to attempt: {len(files)}")

    ib = IB()
    await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, timeout=15, readonly=True)
    log(f"Connected to IBKR (server v{ib.client.serverVersion()})")

    counts = {"complete": 0, "filled": 0, "no_data": 0, "no_work": 0, "error": 0}
    total_filled = 0
    total_still_nan = 0
    failed: list[tuple[str, str]] = []
    start = time.time()

    try:
        for i, parquet_path in enumerate(files, 1):
            ticker = parquet_path.stem
            try:
                status, filled, still_nan = await process_ticker(ib, ticker, parquet_path)
                if status.startswith("error"):
                    counts["error"] += 1
                    failed.append((ticker, status))
                else:
                    counts[status] += 1
                    total_filled += filled
                    total_still_nan += still_nan
            except Exception as e:
                counts["error"] += 1
                failed.append((ticker, str(e)[:80]))

            if i % 50 == 0:
                rate = i / max(time.time() - start, 1e-3)
                eta = (len(files) - i) / max(rate, 1e-3)
                log(
                    f"[{i}/{len(files)}] complete={counts['complete']} filled={counts['filled']} "
                    f"no_data={counts['no_data']} no_work={counts['no_work']} err={counts['error']} "
                    f"| filled_rows={total_filled} | rate={rate:.2f}/s ETA={eta/3600:.1f}h"
                )
    finally:
        ib.disconnect()

    elapsed = time.time() - start
    log("\n=== DONE ===")
    log(
        f"complete={counts['complete']} filled={counts['filled']} no_data={counts['no_data']} "
        f"no_work={counts['no_work']} error={counts['error']}"
    )
    log(f"Total cells filled: {total_filled}, rows still NaN (no IBKR data): {total_still_nan}")
    log(f"Elapsed: {elapsed:.1f}s ({elapsed/3600:.2f}h)")
    if failed:
        out = BARS_DIR.parent / "backfill_metadata_failures.json"
        with out.open("w") as f:
            json.dump(failed, f, indent=2)
        log(f"Wrote {len(failed)} failures to {out}")


if __name__ == "__main__":
    asyncio.run(main())
