"""FastAPI app — exposes signals + scan_runs from SQLite to the React frontend."""
from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..db import connect, init_schema


class SignalOut(BaseModel):
    rank: int
    ticker: str
    close: float
    target_price: float
    stop_price: float
    base_low: float
    base_high: float
    base_weeks: int
    base_depth_pct: float
    pullback_count: int
    volume_ratio: float
    rs_60d: float


class ScanRunOut(BaseModel):
    scan_date: str
    started_at: str
    finished_at: str | None
    universe_size: int
    candidates_count: int
    signals_count: int
    spy_above_200: bool
    duration_seconds: float | None
    error: str | None


class TodayResponse(BaseModel):
    scan_date: str
    spy_above_200: bool
    universe_size: int
    candidates_count: int
    signals_count: int
    signals: list[SignalOut]


app = FastAPI(title="trading-bot scanner API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _init() -> None:
    init_schema()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _latest_scan_date() -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT scan_date FROM scan_runs ORDER BY scan_date DESC LIMIT 1"
        ).fetchone()
    return row["scan_date"] if row else None


def _signals_for(scan_date: str) -> list[SignalOut]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT rank, ticker, close, target_price, stop_price,
                   base_low, base_high, base_weeks, base_depth_pct, pullback_count,
                   volume_ratio, rs_60d
              FROM signals
             WHERE scan_date = ?
             ORDER BY rank
            """,
            (scan_date,),
        ).fetchall()
    return [SignalOut(**dict(r)) for r in rows]


def _scan_run(scan_date: str) -> ScanRunOut | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM scan_runs WHERE scan_date = ?", (scan_date,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["spy_above_200"] = bool(d["spy_above_200"])
    return ScanRunOut(**d)


@app.get("/api/signals/today", response_model=TodayResponse)
def get_today() -> TodayResponse:
    latest = _latest_scan_date()
    if latest is None:
        raise HTTPException(404, "No scan has been run yet")
    return get_for_date(latest)


@app.get("/api/signals/{scan_date}", response_model=TodayResponse)
def get_for_date(scan_date: str) -> TodayResponse:
    try:
        date.fromisoformat(scan_date)
    except ValueError as e:
        raise HTTPException(400, f"Invalid date: {e}")
    run = _scan_run(scan_date)
    if run is None:
        raise HTTPException(404, f"No scan for {scan_date}")
    signals = _signals_for(scan_date)
    return TodayResponse(
        scan_date=scan_date,
        spy_above_200=run.spy_above_200,
        universe_size=run.universe_size,
        candidates_count=run.candidates_count,
        signals_count=run.signals_count,
        signals=signals,
    )


@app.get("/api/scan-runs", response_model=list[ScanRunOut])
def list_scan_runs(limit: int = 30) -> list[ScanRunOut]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scan_runs ORDER BY scan_date DESC LIMIT ?", (limit,)
        ).fetchall()
    out: list[ScanRunOut] = []
    for r in rows:
        d = dict(r)
        d["spy_above_200"] = bool(d["spy_above_200"])
        out.append(ScanRunOut(**d))
    return out
