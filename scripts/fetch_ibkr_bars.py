"""Fetch daily OHLCV bars for the entire US-listed common-stock universe via IBKR.

Connects to the running ib-gateway container on the shared Azure VM at 127.0.0.1:4002
(requires running inside a container with `network_mode: "service:ib-gateway"`, or via
`docker run --network container:ib-gateway`).

Output: one Parquet file per ticker under /data/bars/ with columns:
    date, open, high, low, close, volume, trade_count, wap

Resumable: skips tickers whose Parquet file already exists.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path

import pandas as pd
from ib_async import IB, Stock

OUT = Path(os.environ.get("BARS_OUT", "/data/bars"))
LOG = Path(os.environ.get("BARS_LOG", "/data/logs/fetch_universe.log"))
DURATION = os.environ.get("BARS_DURATION", "1 Y")  # IBKR durationStr
CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "12"))
HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
PORT = int(os.environ.get("IBKR_PORT", "4002"))


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(msg + "\n")


def get_universe() -> list[str]:
    """Pull canonical list of US-listed common stocks from NASDAQ Trader (free, no auth)."""
    log("Fetching universe from NASDAQ Trader...")
    nasdaq_txt = urllib.request.urlopen(
        "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", timeout=30
    ).read().decode()
    other_txt = urllib.request.urlopen(
        "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", timeout=30
    ).read().decode()

    def parse(txt: str, sym_col: str, etf_col: str) -> list[str]:
        lines = txt.strip().split("\n")
        header = lines[0].split("|")
        sym_idx = header.index(sym_col)
        etf_idx = header.index(etf_col) if etf_col in header else None
        out: list[str] = []
        for line in lines[1:]:
            if line.startswith("File Creation"):
                continue
            parts = line.split("|")
            if len(parts) <= sym_idx:
                continue
            sym = parts[sym_idx].strip()
            if not sym:
                continue
            # Skip preferred / warrants / units / rights / when-issued
            if any(c in sym for c in [".", "/", "$", "+", "="]):
                continue
            # Skip ETFs
            if etf_idx is not None and etf_idx < len(parts) and parts[etf_idx].strip() == "Y":
                continue
            out.append(sym)
        return out

    nasdaq = parse(nasdaq_txt, "Symbol", "ETF")
    other = parse(other_txt, "ACT Symbol", "ETF")
    universe = sorted(set(nasdaq + other))
    log(f"Universe: {len(universe)} (NASDAQ {len(nasdaq)}, Other {len(other)})")
    return universe


async def fetch_ticker(ib: IB, ticker: str) -> pd.DataFrame | None:
    contract = Stock(ticker, "SMART", "USD")
    bars = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime="",
        durationStr=DURATION,
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
    )
    if not bars or len(bars) < 30:
        return None
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


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    universe = get_universe()
    pd.DataFrame({"symbol": universe}).to_parquet(OUT.parent / "universe.parquet")

    ib = IB()
    await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, timeout=15)
    log(f"Connected to IBKR (server v{ib.client.serverVersion()})")

    saved = 0
    skipped = 0
    failed: list[tuple[str, str]] = []
    start = time.time()

    for i, ticker in enumerate(universe, 1):
        out_path = OUT / f"{ticker}.parquet"
        if out_path.exists():
            skipped += 1
            continue
        try:
            df = await fetch_ticker(ib, ticker)
            if df is None:
                failed.append((ticker, "empty_or_too_few"))
                continue
            df.to_parquet(out_path, compression="snappy")
            saved += 1
            if i % 100 == 0:
                rate = (saved + skipped) / max(time.time() - start, 1e-3)
                eta = (len(universe) - i) / max(rate, 1e-3)
                log(f"[{i}/{len(universe)}] saved={saved} skipped={skipped} failed={len(failed)} | rate={rate:.1f}/s ETA={eta/60:.1f}min")
        except Exception as e:
            failed.append((ticker, str(e)[:80]))

    elapsed = time.time() - start
    ib.disconnect()

    log("\n=== DONE ===")
    log(f"Saved: {saved}, Skipped: {skipped}, Failed: {len(failed)}")
    log(f"Elapsed: {elapsed:.1f}s ({elapsed/60:.1f}min)")
    log(f"Total disk: {sum(p.stat().st_size for p in OUT.glob('*.parquet'))/1024/1024:.1f} MB")
    if failed:
        with (OUT.parent / "fetch_failures.json").open("w") as f:
            json.dump(failed, f, indent=2)
        log("Failure details written to fetch_failures.json")


if __name__ == "__main__":
    asyncio.run(main())
