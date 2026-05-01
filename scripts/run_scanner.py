"""CLI entry — run today's scan, write to SQLite, print summary.

Usage:
    uv run python scripts/run_scanner.py            # scan latest available date
    uv run python scripts/run_scanner.py 2026-04-29 # scan a specific date
"""
from __future__ import annotations

import sys
from datetime import date

from trading_bot.runner import run


def main() -> None:
    scan_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    result = run(scan_date=scan_date)

    print(f"\n{'='*60}")
    print(f"  VCP Scanner — {result.scan_date}")
    print(f"{'='*60}")
    print(f"Universe:          {result.universe_size:,} tickers")
    print(f"SPY above 200d MA: {'yes' if result.spy_above_200 else 'NO — no entries'}")
    print(f"Candidates:        {result.candidates_count}")
    print(f"Signals (ranked):  {len(result.signals)}")
    print(f"Duration:          {result.duration_seconds:.1f}s")
    if result.error:
        print(f"Error:             {result.error}")

    if result.signals:
        print(f"\n  {'rank':>4}  {'ticker':<8} {'close':>9} {'target':>9} {'stop':>9} {'vol×':>5} {'base':>5} {'depth%':>6}")
        print(f"  {'-'*4}  {'-'*8} {'-'*9} {'-'*9} {'-'*9} {'-'*5} {'-'*5} {'-'*6}")
        for s in result.signals[:20]:
            print(
                f"  {s.rank:>4}  {s.ticker:<8} ${s.close:>8.2f} ${s.target_price:>8.2f} ${s.stop_price:>8.2f}"
                f" {s.volume_ratio:>5.2f} {s.base_weeks:>4}w {s.base_depth_pct*100:>5.1f}%"
            )
        if len(result.signals) > 20:
            print(f"  ... and {len(result.signals) - 20} more (see SQLite)")


if __name__ == "__main__":
    main()
