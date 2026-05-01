"""Weekend MVP backtest — validates the position-trading strategy on historical data.

Loads all Parquet bars from /data/bars/, runs the 7-filter scanner forward in
time from 2012-2026, simulates trades with realistic costs, and reports
whether the strategy has real edge (expectancy > +0.3R) or is overfit.

This is intentionally a single-file script. NOT production code — production
goes in src/ later. Goal is the fastest possible go/no-go decision.

Strategy spec (from README.md):
  - Universe: price $10-$200, ADV > 500k shares
  - Trend filters: close > MA50, MA50 > MA200, RS vs SPY > 0 over 60d
  - Setup: 5-15w base, 15-25% depth, contracted volume, VCP pattern
  - Trigger: close > base_high (pivot), volume >= 1.5x avg, close in upper third
  - Stop: 10% below entry
  - Target: 30% above entry
  - Time stop: 12 weeks
  - Max 5 open positions, 2 entries/week
  - Risk 1% per trade
  - Pause when SPY < 200d MA
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BARS_DIR = Path("/data/bars")
SPY_TICKER = "SPY"

# Strategy parameters
BACKTEST_START = pd.Timestamp("2012-01-01")
BACKTEST_END = pd.Timestamp("2026-04-29")
INITIAL_CAPITAL = 10_000.0
RISK_PER_TRADE = 0.01  # 1% of account
FIXED_DOLLAR_PER_TRADE = 1000.0  # if >0, use this instead of risk-based sizing
MAX_POSITIONS = 10  # raised from 5 since we have $1k slots
MAX_ENTRIES_PER_WEEK = 5  # raised — fixed sizing means we can take more shots
STOP_PCT = 0.10
TARGET_PCT = 0.30
TIME_STOP_DAYS = 120  # effectively no time stop — hold until target or stop
SLIPPAGE_ENTRY = 0.002  # 0.2%
SLIPPAGE_EXIT = 0.001  # 0.1%
COMMISSION_PER_SHARE = 0.005

# Filters
MIN_PRICE = 10.0
MAX_PRICE = 200.0
MIN_ADV = 500_000
BASE_MIN_WEEKS = 5
BASE_MAX_WEEKS = 15
BASE_MIN_DEPTH = 0.10
BASE_MAX_DEPTH = 0.25
VCP_MIN_PULLBACKS = 3
VOLUME_MULTIPLIER = 1.5


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    base_low: float
    base_high: float  # pivot
    base_weeks: int
    breakout_volume_ratio: float
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    pnl_dollars: float = 0.0
    r_multiple: float = 0.0


def load_bars() -> dict[str, pd.DataFrame]:
    """Load all parquet bars into memory, indexed by date."""
    print(f"Loading bars from {BARS_DIR}...")
    bars: dict[str, pd.DataFrame] = {}
    files = sorted(BARS_DIR.glob("*.parquet"))
    t0 = time.time()
    for f in files:
        df = pd.read_parquet(f, columns=["date", "open", "high", "low", "close", "volume"])
        if df.empty or len(df) < 250:  # need at least 1 year for filters
            continue
        df["date"] = pd.to_datetime(df["date"].astype(str))
        df = df.dropna().drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
        df.set_index("date", inplace=True)
        bars[f.stem] = df
    print(f"Loaded {len(bars)} tickers in {time.time()-t0:.1f}s")
    return bars


def precompute_indicators(bars: dict[str, pd.DataFrame], spy: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Add MA50, MA200, vol_avg50, rs_60d (vs SPY) to each ticker's DataFrame."""
    print("Pre-computing indicators...")
    spy_close = spy["close"]
    t0 = time.time()
    for sym, df in bars.items():
        df["ma50"] = df["close"].rolling(50, min_periods=50).mean()
        df["ma200"] = df["close"].rolling(200, min_periods=200).mean()
        df["vol_avg50"] = df["volume"].rolling(50, min_periods=50).mean()
        # Aligned SPY for RS calculation
        spy_aligned = spy_close.reindex(df.index, method="ffill")
        # 60-day return: (close[t] / close[t-60]) - (spy[t] / spy[t-60])
        df["rs_60d"] = (df["close"] / df["close"].shift(60)) - (spy_aligned / spy_aligned.shift(60))
    print(f"Indicators computed in {time.time()-t0:.1f}s")
    return bars


def detect_vcp_base(df: pd.DataFrame, end_idx: int) -> dict[str, Any] | None:
    """Detect a VCP base ending at end_idx. Returns dict with base info or None.

    Looks for: 5-15 week consolidation, depth 10-25%, volume contraction, ≥3 progressively
    tighter pullbacks within the base.
    """
    for length_days in range(BASE_MIN_WEEKS * 5, BASE_MAX_WEEKS * 5 + 1, 5):
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
        if not (BASE_MIN_DEPTH <= depth <= BASE_MAX_DEPTH):
            continue

        # Volume contraction: avg in base < avg in prior 60 days
        prior = df.iloc[max(0, end_idx - length_days - 60) : end_idx - length_days]
        if prior.empty or prior["volume"].mean() <= window["volume"].mean():
            continue

        # VCP: count progressively tighter pullbacks
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

        if len(peaks) < VCP_MIN_PULLBACKS:
            continue

        # Check progressively tighter
        pullback_depths = []
        for i, peak_idx in enumerate(peaks):
            if i == 0:
                continue
            trough = closes[peaks[i-1]:peak_idx].min()
            d = (closes[peaks[i-1]] - trough) / closes[peaks[i-1]]
            pullback_depths.append(d)
        # Last pullback should be smaller than first
        if len(pullback_depths) >= 2 and pullback_depths[-1] < pullback_depths[0]:
            return {
                "high": float(high),
                "low": float(low),
                "weeks": length_days // 5,
                "pullback_count": len(peaks),
            }
    return None


def is_breakout(df: pd.DataFrame, idx: int, pivot: float) -> tuple[bool, float]:
    """Returns (is_breakout, volume_ratio)."""
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
        and vol_ratio >= VOLUME_MULTIPLIER
        and close_in_top_third
    ), vol_ratio


def passes_universe(today: pd.Series) -> bool:
    if pd.isna(today.get("vol_avg50")):
        return False
    return MIN_PRICE <= today["close"] <= MAX_PRICE and today["vol_avg50"] >= MIN_ADV


def passes_trend(today: pd.Series) -> bool:
    return (
        not pd.isna(today.get("ma50"))
        and not pd.isna(today.get("ma200"))
        and not pd.isna(today.get("rs_60d"))
        and today["close"] > today["ma50"]
        and today["ma50"] > today["ma200"]
        and today["rs_60d"] > 0
    )


def run_backtest(bars: dict[str, pd.DataFrame], spy: pd.DataFrame) -> tuple[list[Trade], pd.DataFrame]:
    print(f"\nRunning backtest from {BACKTEST_START.date()} to {BACKTEST_END.date()}")

    spy["spy_ma200"] = spy["close"].rolling(200, min_periods=200).mean()
    trading_days = pd.bdate_range(BACKTEST_START, BACKTEST_END)

    capital = INITIAL_CAPITAL
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    open_positions: dict[str, Trade] = {}
    closed_trades: list[Trade] = []
    weekly_entries: dict[pd.Timestamp, int] = {}  # week_start → count
    candidates_per_day: list[int] = []

    t0 = time.time()
    for day_idx, today_date in enumerate(trading_days):
        # SPY regime check
        if today_date not in spy.index:
            continue
        spy_row = spy.loc[today_date]
        if pd.isna(spy_row.get("spy_ma200")):
            continue
        spy_above_200 = spy_row["close"] > spy_row["spy_ma200"]

        # 1. Check exits on open positions
        to_close = []
        for sym, trade in open_positions.items():
            if sym not in bars:
                continue
            df = bars[sym]
            if today_date not in df.index:
                continue
            row = df.loc[today_date]
            held_days = (today_date - trade.entry_date).days

            # Check stop first (intraday low)
            if row["low"] <= trade.stop_price:
                exit_price = trade.stop_price * (1 - SLIPPAGE_EXIT)
                trade.exit_reason = "stop"
            elif row["high"] >= trade.target_price:
                exit_price = trade.target_price * (1 - SLIPPAGE_EXIT)
                trade.exit_reason = "target"
            elif held_days >= TIME_STOP_DAYS:
                exit_price = row["close"] * (1 - SLIPPAGE_EXIT)
                trade.exit_reason = "time"
            else:
                continue

            trade.exit_date = today_date
            trade.exit_price = float(exit_price)
            trade.pnl_dollars = (trade.exit_price - trade.entry_price) * trade.shares - 2 * trade.shares * COMMISSION_PER_SHARE
            risk_per_share = trade.entry_price - trade.stop_price
            trade.r_multiple = (trade.exit_price - trade.entry_price) / risk_per_share if risk_per_share > 0 else 0.0
            capital += trade.pnl_dollars + trade.shares * trade.entry_price  # return position value to cash
            closed_trades.append(trade)
            to_close.append(sym)
        for sym in to_close:
            del open_positions[sym]

        # 2. Check entries (only if regime favorable + capacity available)
        if not spy_above_200 or len(open_positions) >= MAX_POSITIONS:
            equity_curve.append((today_date, mark_to_market(capital, open_positions, bars, today_date)))
            continue

        week_start = today_date - timedelta(days=today_date.weekday())
        if weekly_entries.get(week_start, 0) >= MAX_ENTRIES_PER_WEEK:
            equity_curve.append((today_date, mark_to_market(capital, open_positions, bars, today_date)))
            continue

        # Phase 1: COLLECT all candidates with signal strength
        candidates = []  # (sym, df, idx, base, vol_ratio)
        for sym, df in bars.items():
            if sym in open_positions:
                continue
            if today_date not in df.index:
                continue
            try:
                idx = df.index.get_loc(today_date)
            except KeyError:
                continue
            if idx < 250:
                continue
            today_row = df.iloc[idx]
            if not passes_universe(today_row):
                continue
            if not passes_trend(today_row):
                continue
            base = detect_vcp_base(df, idx)
            if base is None:
                continue
            triggered, vol_ratio = is_breakout(df, idx, base["high"])
            if not triggered:
                continue
            if idx + 1 >= len(df):
                continue
            candidates.append((sym, df, idx, base, vol_ratio))

        cands_today = len(candidates)

        # Phase 2: RANK by signal strength (highest volume ratio = strongest breakout)
        candidates.sort(key=lambda c: c[4], reverse=True)

        # Phase 3: BUY best candidates until slot/budget limits hit
        for sym, df, idx, base, vol_ratio in candidates:
            next_row = df.iloc[idx + 1]
            entry = float(next_row["open"]) * (1 + SLIPPAGE_ENTRY)
            stop = entry * (1 - STOP_PCT)
            target = entry * (1 + TARGET_PCT)
            risk_per_share = entry - stop
            if FIXED_DOLLAR_PER_TRADE > 0:
                shares = int(FIXED_DOLLAR_PER_TRADE / entry)
            else:
                shares = int(min(capital * RISK_PER_TRADE / risk_per_share, capital * 0.15 / entry))
            if shares <= 0 or shares * entry > capital:
                continue
            trade = Trade(
                symbol=sym,
                entry_date=df.index[idx + 1],
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                shares=shares,
                base_low=base["low"],
                base_high=base["high"],
                base_weeks=base["weeks"],
                breakout_volume_ratio=vol_ratio,
            )
            capital -= shares * entry  # cash deducted
            open_positions[sym] = trade
            weekly_entries[week_start] = weekly_entries.get(week_start, 0) + 1

            if len(open_positions) >= MAX_POSITIONS:
                break
            if weekly_entries.get(week_start, 0) >= MAX_ENTRIES_PER_WEEK:
                break

        candidates_per_day.append(cands_today)
        equity_curve.append((today_date, mark_to_market(capital, open_positions, bars, today_date)))

        if day_idx % 250 == 0:
            elapsed = time.time() - t0
            print(f"  [{today_date.date()}] {len(closed_trades)} closed, {len(open_positions)} open, equity ${equity_curve[-1][1]:,.0f} ({elapsed:.0f}s)")

    print(f"\nBacktest done in {time.time()-t0:.1f}s")

    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"]).set_index("date")
    return closed_trades, eq_df


def mark_to_market(cash: float, open_pos: dict[str, Trade], bars: dict[str, pd.DataFrame], today: pd.Timestamp) -> float:
    total = cash
    for sym, trade in open_pos.items():
        if sym in bars and today in bars[sym].index:
            total += trade.shares * float(bars[sym].loc[today, "close"])
        else:
            total += trade.shares * trade.entry_price
    return total


def report(trades: list[Trade], equity: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    if not trades:
        print("No trades — strategy never fired.")
        return

    df = pd.DataFrame([{
        "symbol": t.symbol, "entry_date": t.entry_date, "exit_date": t.exit_date,
        "exit_reason": t.exit_reason, "r_multiple": t.r_multiple, "pnl": t.pnl_dollars,
        "held_days": (t.exit_date - t.entry_date).days if t.exit_date else 0,
    } for t in trades])

    n = len(df)
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    win_rate = len(wins) / n
    avg_win_r = wins["r_multiple"].mean() if len(wins) else 0
    avg_loss_r = losses["r_multiple"].mean() if len(losses) else 0
    expectancy = win_rate * avg_win_r + (1 - win_rate) * avg_loss_r

    print(f"\nTotal trades:    {n}")
    print(f"Win rate:        {win_rate:.1%}")
    print(f"Avg win:         {avg_win_r:+.2f}R")
    print(f"Avg loss:        {avg_loss_r:+.2f}R")
    print(f"Expectancy:      {expectancy:+.2f}R per trade")
    print(f"Avg hold:        {df['held_days'].mean():.0f} days")

    print(f"\nExit reasons:")
    print(df["exit_reason"].value_counts().to_string())

    print(f"\nFinal equity:    ${equity['equity'].iloc[-1]:,.0f}")
    total_return = equity["equity"].iloc[-1] / INITIAL_CAPITAL - 1
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity["equity"].iloc[-1] / INITIAL_CAPITAL) ** (1/years) - 1 if years > 0 else 0
    print(f"Total return:    {total_return:+.1%}")
    print(f"CAGR:            {cagr:+.1%}")

    # Drawdown
    rolling_max = equity["equity"].cummax()
    dd = (equity["equity"] - rolling_max) / rolling_max
    max_dd = dd.min()
    print(f"Max drawdown:    {max_dd:.1%}")

    # Sharpe (daily)
    daily_ret = equity["equity"].pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    print(f"Sharpe (daily):  {sharpe:.2f}")

    print("\n" + "=" * 60)
    print("  GO / NO-GO")
    print("=" * 60)
    if expectancy > 0.30 and win_rate > 0.30:
        print(f"GO — expectancy {expectancy:+.2f}R per trade is profitable. Strategy has edge.")
    elif expectancy > 0.10:
        print(f"MAYBE — expectancy {expectancy:+.2f}R is positive but thin. Consider parameter tuning.")
    else:
        print(f"NO-GO — expectancy {expectancy:+.2f}R is negative or near zero. Strategy not viable.")


def main() -> None:
    bars = load_bars()
    if SPY_TICKER not in bars:
        # Fallback: try to load SPY directly
        spy_path = BARS_DIR / "SPY.parquet"
        if spy_path.exists():
            spy = pd.read_parquet(spy_path)
            spy["date"] = pd.to_datetime(spy["date"].astype(str))
            spy.set_index("date", inplace=True)
        else:
            raise SystemExit("No SPY data — backtest needs SPY for regime + RS")
    else:
        spy = bars[SPY_TICKER]

    bars = precompute_indicators(bars, spy)
    trades, equity = run_backtest(bars, spy)
    report(trades, equity)


if __name__ == "__main__":
    main()
