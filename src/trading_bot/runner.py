"""Scan orchestrator — runs `scanner.scan()` and persists results to SQLite."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from .db import connect, init_schema
from .scanner import ScanResult, scan


def persist(result: ScanResult, *, started_at: datetime) -> None:
    init_schema()
    finished = datetime.now()
    with connect() as conn:
        conn.execute(
            "DELETE FROM signals WHERE scan_date = ?", (result.scan_date.isoformat(),)
        )
        conn.executemany(
            """
            INSERT INTO signals (
                scan_date, ticker, close, target_price, stop_price,
                base_low, base_high, base_weeks, base_depth_pct, pullback_count,
                volume_ratio, rs_60d, above_ma50, above_ma200, spy_above_200, rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    result.scan_date.isoformat(),
                    s.ticker,
                    s.close,
                    s.target_price,
                    s.stop_price,
                    s.base_low,
                    s.base_high,
                    s.base_weeks,
                    s.base_depth_pct,
                    s.pullback_count,
                    s.volume_ratio,
                    s.rs_60d,
                    int(s.above_ma50),
                    int(s.above_ma200),
                    int(s.spy_above_200),
                    s.rank,
                )
                for s in result.signals
            ],
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO scan_runs (
                scan_date, started_at, finished_at, universe_size,
                candidates_count, signals_count, spy_above_200,
                duration_seconds, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.scan_date.isoformat(),
                started_at.isoformat(),
                finished.isoformat(),
                result.universe_size,
                result.candidates_count,
                len(result.signals),
                int(result.spy_above_200),
                result.duration_seconds,
                result.error,
            ),
        )


def run(bars_dir: Path | None = None, scan_date: date | None = None) -> ScanResult:
    started = datetime.now()
    result = scan(bars_dir, scan_date)
    persist(result, started_at=started)
    return result
