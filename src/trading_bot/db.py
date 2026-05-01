"""SQLite layer — schema, connection, and signal upserts.

The schema below is the contract between scanner (writer) and API (reader).
It must support these queries efficiently:

    - "Today's ranked signals"  (filter by scan_date, order by volume_ratio DESC)
    - "Signals for a given date" (filter by scan_date)
    - "All scans for a ticker"  (filter by ticker, order by scan_date DESC)
    - "Universe stats per scan" (count of signals per scan_date)

═══════════════════════════════════════════════════════════════════════════
DECISION POINT — schema design
═══════════════════════════════════════════════════════════════════════════
The user owns this design choice. The schema below is a starting sketch.
Refine the columns, types, indexes, and constraints to match how you want
to query / display signals on the frontend.

Things to think about:
    - Primary key:   (scan_date, ticker) composite, or surrogate INTEGER id?
    - Volume ratio:  REAL — what precision matters?
    - Base bounds:   keep base_low / base_high / base_weeks for chart drawing?
    - Status:        track lifecycle (NEW / TAKEN / SKIPPED / EXPIRED) here, or
                     in a separate trades table?
    - Indexes:       needed on scan_date for daily queries; ticker for history
    - Soft-delete?   Or just overwrite same-day rescan results?
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    scan_date         TEXT    NOT NULL,
    ticker            TEXT    NOT NULL,
    close             REAL    NOT NULL,
    target_price      REAL    NOT NULL,
    stop_price        REAL    NOT NULL,
    base_low          REAL    NOT NULL,
    base_high         REAL    NOT NULL,
    base_weeks        INTEGER NOT NULL,
    base_depth_pct    REAL    NOT NULL,
    pullback_count    INTEGER NOT NULL,
    volume_ratio      REAL    NOT NULL,
    rs_60d            REAL    NOT NULL,
    above_ma50        INTEGER NOT NULL,
    above_ma200       INTEGER NOT NULL,
    spy_above_200     INTEGER NOT NULL,
    rank              INTEGER NOT NULL,
    PRIMARY KEY (scan_date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_signals_date   ON signals (scan_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals (ticker, scan_date DESC);

CREATE TABLE IF NOT EXISTS scan_runs (
    scan_date           TEXT    PRIMARY KEY,
    started_at          TEXT    NOT NULL,
    finished_at         TEXT,
    universe_size       INTEGER NOT NULL,
    candidates_count    INTEGER NOT NULL,
    signals_count       INTEGER NOT NULL,
    spy_above_200       INTEGER NOT NULL,
    duration_seconds    REAL,
    error               TEXT
);
"""


@contextmanager
def connect(db_path: Path | None = None):
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
