"""Daily VCP scanner — applies the 11 buy conditions to today's market.

Lifted from scripts/mvp_backtest.py. Same logic, single-day scope:
no historical loop, no position management — just "given today's bars, which
tickers fire all 11 conditions and how strong is each signal?"
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import settings


SPY_TICKER = "SPY"


@dataclass
class Signal:
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
    above_ma50: bool
    above_ma200: bool
    spy_above_200: bool
    rank: int = 0


@dataclass
class ScanResult:
    scan_date: date
    universe_size: int
    candidates_count: int
    signals: list[Signal]
    spy_above_200: bool
    duration_seconds: float = 0.0
    error: str | None = None


def load_bars(bars_dir: Path) -> dict[str, pd.DataFrame]:
    bars: dict[str, pd.DataFrame] = {}
    files = sorted(bars_dir.glob("*.parquet"))
    for f in files:
        df = pd.read_parquet(f, columns=["date", "open", "high", "low", "close", "volume"])
        if df.empty or len(df) < 250:
            continue
        df["date"] = pd.to_datetime(df["date"].astype(str))
        df = (
            df.dropna()
            .drop_duplicates(subset="date")
            .sort_values("date")
            .reset_index(drop=True)
        )
        df.set_index("date", inplace=True)
        bars[f.stem] = df
    return bars


def precompute_indicators(
    bars: dict[str, pd.DataFrame], spy: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    spy_close = spy["close"]
    for df in bars.values():
        df["ma50"] = df["close"].rolling(50, min_periods=50).mean()
        df["ma200"] = df["close"].rolling(200, min_periods=200).mean()
        df["vol_avg50"] = df["volume"].rolling(50, min_periods=50).mean()
        spy_aligned = spy_close.reindex(df.index, method="ffill")
        df["rs_60d"] = (df["close"] / df["close"].shift(60)) - (
            spy_aligned / spy_aligned.shift(60)
        )
    return bars


def detect_vcp_base(df: pd.DataFrame, end_idx: int) -> dict[str, Any] | None:
    """Returns base info dict or None — 5-15w, 10-25% depth, vol-contracting, ≥3 tighter pullbacks."""
    for length_days in range(
        settings.base_min_weeks * 5, settings.base_max_weeks * 5 + 1, 5
    ):
        if end_idx < length_days + 250:
            continue
        window = df.iloc[end_idx - length_days : end_idx]
        if window.empty:
            continue
        high = window["close"].max()
        low = window["close"].min()
        if low <= 0:
            continue
        depth = (high - low) / low
        if not (settings.base_min_depth <= depth <= settings.base_max_depth):
            continue

        prior = df.iloc[max(0, end_idx - length_days - 60) : end_idx - length_days]
        if prior.empty or prior["volume"].mean() <= window["volume"].mean():
            continue

        closes = window["close"].values
        peaks: list[int] = []
        running_max = closes[0]
        running_max_idx = 0
        for i in range(1, len(closes)):
            if closes[i] >= running_max:
                running_max = closes[i]
                running_max_idx = i
            elif i - running_max_idx > 3 and (running_max - closes[i]) / running_max > 0.02:
                peaks.append(running_max_idx)
                running_max = closes[i]
                running_max_idx = i

        if len(peaks) < settings.vcp_min_pullbacks:
            continue

        pullback_depths = []
        for i, peak_idx in enumerate(peaks):
            if i == 0:
                continue
            trough = closes[peaks[i - 1] : peak_idx].min()
            d = (closes[peaks[i - 1]] - trough) / closes[peaks[i - 1]]
            pullback_depths.append(d)
        if len(pullback_depths) >= 2 and pullback_depths[-1] < pullback_depths[0]:
            return {
                "high": float(high),
                "low": float(low),
                "weeks": length_days // 5,
                "depth_pct": float(depth),
                "pullback_count": len(peaks),
            }
    return None


def is_breakout(df: pd.DataFrame, idx: int, pivot: float) -> tuple[bool, float]:
    today = df.iloc[idx]
    avg_vol = today.get("vol_avg50", np.nan)
    if pd.isna(avg_vol) or avg_vol <= 0:
        return False, 0.0
    vol_ratio = today["volume"] / avg_vol
    today_range = today["high"] - today["low"]
    close_in_top_third = (
        today["close"] >= today["low"] + 0.66 * today_range if today_range > 0 else False
    )
    return (
        today["close"] > pivot
        and vol_ratio >= settings.volume_multiplier
        and close_in_top_third
    ), float(vol_ratio)


def passes_universe(today: pd.Series) -> bool:
    if pd.isna(today.get("vol_avg50")):
        return False
    return (
        settings.min_price <= today["close"] <= settings.max_price
        and today["vol_avg50"] >= settings.min_adv
    )


def passes_trend(today: pd.Series) -> bool:
    return (
        not pd.isna(today.get("ma50"))
        and not pd.isna(today.get("ma200"))
        and not pd.isna(today.get("rs_60d"))
        and today["close"] > today["ma50"]
        and today["ma50"] > today["ma200"]
        and today["rs_60d"] > 0
    )


def scan(
    bars_dir: Path | None = None, scan_date: date | None = None
) -> ScanResult:
    """Scan all parquet bars for VCP signals on `scan_date` (default: latest available)."""
    t0 = time.time()
    bars = load_bars(bars_dir or settings.bars_dir)

    if SPY_TICKER not in bars:
        return ScanResult(
            scan_date=scan_date or date.today(),
            universe_size=len(bars),
            candidates_count=0,
            signals=[],
            spy_above_200=False,
            duration_seconds=time.time() - t0,
            error=f"{SPY_TICKER} not found in bars",
        )

    spy = bars[SPY_TICKER]
    spy["spy_ma200"] = spy["close"].rolling(200, min_periods=200).mean()
    bars = precompute_indicators(bars, spy)

    target_date = pd.Timestamp(scan_date) if scan_date else spy.index[-1]
    if target_date not in spy.index:
        return ScanResult(
            scan_date=target_date.date(),
            universe_size=len(bars),
            candidates_count=0,
            signals=[],
            spy_above_200=False,
            duration_seconds=time.time() - t0,
            error=f"No SPY bar for {target_date.date()}",
        )

    spy_row = spy.loc[target_date]
    spy_above_200 = bool(
        not pd.isna(spy_row.get("spy_ma200"))
        and spy_row["close"] > spy_row["spy_ma200"]
    )

    candidates: list[tuple[str, dict[str, Any], float, float, float]] = []
    if spy_above_200:
        for sym, df in bars.items():
            if sym == SPY_TICKER or target_date not in df.index:
                continue
            try:
                idx = df.index.get_loc(target_date)
            except KeyError:
                continue
            if idx < 250:
                continue
            today_row = df.iloc[idx]
            if not (passes_universe(today_row) and passes_trend(today_row)):
                continue
            base = detect_vcp_base(df, idx)
            if base is None:
                continue
            triggered, vol_ratio = is_breakout(df, idx, base["high"])
            if not triggered:
                continue
            candidates.append((sym, base, vol_ratio, float(today_row["close"]), float(today_row["rs_60d"])))

    candidates.sort(key=lambda c: c[2], reverse=True)

    signals: list[Signal] = []
    for rank, (sym, base, vol_ratio, close, rs_60d) in enumerate(candidates, start=1):
        signals.append(
            Signal(
                ticker=sym,
                close=close,
                target_price=close * (1 + settings.target_pct),
                stop_price=close * (1 - settings.stop_pct),
                base_low=base["low"],
                base_high=base["high"],
                base_weeks=base["weeks"],
                base_depth_pct=base["depth_pct"],
                pullback_count=base["pullback_count"],
                volume_ratio=vol_ratio,
                rs_60d=rs_60d,
                above_ma50=True,
                above_ma200=True,
                spy_above_200=True,
                rank=rank,
            )
        )

    return ScanResult(
        scan_date=target_date.date(),
        universe_size=len(bars),
        candidates_count=len(candidates),
        signals=signals,
        spy_above_200=spy_above_200,
        duration_seconds=time.time() - t0,
    )
