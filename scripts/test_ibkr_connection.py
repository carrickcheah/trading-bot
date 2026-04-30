"""Smoke test: connect to running ib-gateway, fetch 1 year of AAPL daily bars,
verify all 8 fields (OHLCV + trade_count + wap) are present.

Run inside a container that joins ib-gateway's network namespace:
    docker run --rm \
        --network container:ib-gateway \
        -v $(pwd)/scripts/test_ibkr_connection.py:/test.py:ro \
        python:3.12-slim \
        bash -c 'pip install -q ib_async && python /test.py'
"""
from __future__ import annotations

import asyncio

from ib_async import IB, Stock


async def main() -> None:
    ib = IB()
    await ib.connectAsync("127.0.0.1", 4002, clientId=12, timeout=15)
    print(f"Connected. Server version: {ib.client.serverVersion()}")

    contract = Stock("AAPL", "SMART", "USD")
    bars = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime="",
        durationStr="1 Y",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
    )
    print(f"Got {len(bars)} bars for AAPL")
    if bars:
        b = bars[-1]
        print("Last bar fields:")
        for attr in ("date", "open", "high", "low", "close", "volume", "barCount", "average"):
            print(f"  {attr}: {getattr(b, attr)}")
    ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
