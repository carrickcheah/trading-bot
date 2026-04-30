"""Phase B: backfill historical daily bars to ~15 years per ticker.

For each ticker that already has Phase A (1y) data, walks backward in 1-year
chunks via IBKR `reqHistoricalData` (each request endDateTime = previous chunk's
oldest date - 1 day). Combines all chunks into a single Parquet file with
deduped, sorted dates.

Designed to be triggered by the watchdog after Phase A finishes. Idempotent:
skips tickers that already have ≥1500 rows (~6 years) — re-run safely.
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
LOG = Path(os.environ.get("BARS_LOG", "/data/logs/fetch_15y.log"))
CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "12"))
HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
PORT = int(os.environ.get("IBKR_PORT", "4002"))
MAX_CHUNKS = int(os.environ.get("MAX_CHUNKS", "15"))  # 15 yearly chunks => ~15y history
MIN_BARS_PER_CHUNK = int(os.environ.get("MIN_BARS_PER_CHUNK", "100"))


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


async def fetch_chunk(ib: IB, contract: Stock, end_dt: str) -> pd.DataFrame | None:
    bars = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime=end_dt,
        durationStr="1 Y",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
    )
    if not bars:
        return None
    return bars_to_df(bars)


async def fetch_full_history(ib: IB, ticker: str) -> pd.DataFrame | None:
    contract = Stock(ticker, "SMART", "USD")
    chunks: list[pd.DataFrame] = []
    end_dt = ""  # start from now
    for chunk_idx in range(MAX_CHUNKS):
        try:
            df = await fetch_chunk(ib, contract, end_dt)
        except Exception:
            break
        if df is None or df.empty:
            break
        chunks.append(df)
        # If this chunk is short, we likely hit the IPO/listing start
        if len(df) < MIN_BARS_PER_CHUNK:
            break
        oldest = pd.to_datetime(df["date"].min())
        end_dt = (oldest - timedelta(days=1)).strftime("%Y%m%d 23:59:59")
    if not chunks:
        return None
    combined = (
        pd.concat(chunks, ignore_index=True)
        .drop_duplicates(subset="date")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return combined


async def main() -> None:
    tickers = sorted(p.stem for p in BARS_DIR.glob("*.parquet"))
    log(f"Phase B start: {len(tickers)} tickers to backfill")

    ib = IB()
    await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, timeout=15)
    log(f"Connected to IBKR (server v{ib.client.serverVersion()})")

    upgraded = 0
    skipped = 0
    failed: list[tuple[str, str]] = []
    start = time.time()

    for i, ticker in enumerate(tickers, 1):
        out = BARS_DIR / f"{ticker}.parquet"
        try:
            existing = pd.read_parquet(out)
            if len(existing) >= 1500:
                skipped += 1
                continue
        except Exception:
            pass
        try:
            full = await fetch_full_history(ib, ticker)
            if full is None or len(full) < 200:
                failed.append((ticker, f"only {0 if full is None else len(full)} bars"))
                continue
            full.to_parquet(out, compression="snappy")
            upgraded += 1
            if i % 50 == 0:
                rate = (upgraded + skipped) / max(time.time() - start, 1e-3)
                eta = (len(tickers) - i) / max(rate, 1e-3)
                log(f"[{i}/{len(tickers)}] upgraded={upgraded} skipped={skipped} failed={len(failed)} | rate={rate:.2f}/s ETA={eta/60:.1f}min")
        except Exception as e:
            failed.append((ticker, str(e)[:80]))

    elapsed = time.time() - start
    ib.disconnect()
    log("\n=== PHASE B DONE ===")
    log(f"Upgraded: {upgraded}, Skipped: {skipped}, Failed: {len(failed)}")
    log(f"Elapsed: {elapsed:.1f}s ({elapsed/60:.1f}min)")
    log(f"Total disk: {sum(p.stat().st_size for p in BARS_DIR.glob('*.parquet')) / 1024 / 1024:.1f} MB")
    if failed:
        with (BARS_DIR.parent / "fetch_15y_failures.json").open("w") as f:
            json.dump(failed, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
