"""Cross-Sectional Momentum (CSM 12-1) backtest — sensitivity test variant 7%/21%.

Strategy:
  - Each month-end: rank ALL tickers by 12-month return (t-13mo → t-1mo)
  - Take top 10 (skipping any currently held)
  - At next month open: BUY top-10 candidates at $1,000 each
  - Each position has:
      stop:    entry × 0.93  (7% below)
      target:  entry × 1.21  (21% above)
      time:    90 days max
  - Exit when stop / target / time hits (whichever first)
  - Open positions are NOT force-closed on rebalance (managed independently)

Universe filters:
  - Price between $10 and $200 at signal date
  - 50-day avg volume > 500k shares
  - At least 270 trading days of history (need 12-month lookback)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BARS_DIR = Path("/data/bars")
SPY_TICKER = "SPY"

# Backtest parameters
BACKTEST_START = pd.Timestamp("2012-01-01")
BACKTEST_END = pd.Timestamp("2026-04-29")
INITIAL_CAPITAL = 10_000.0
DOLLAR_PER_TRADE = 1_000.0
MAX_POSITIONS = 10
TOP_N = 10  # take top N by momentum each month

# Stop/target/time
STOP_PCT = 0.07  # 7%
TARGET_PCT = 0.21  # 21% (1:3 R:R)
TIME_STOP_DAYS = 90  # 3 months

# Costs
SLIPPAGE_ENTRY = 0.002
SLIPPAGE_EXIT = 0.001
COMMISSION_PER_SHARE = 0.005

# Filters
MIN_PRICE = 10.0
MAX_PRICE = 200.0
MIN_ADV = 500_000
MIN_HISTORY = 270  # ~13 months


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    momentum_12_1: float  # the 12-1 return that put it in top N
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    pnl_dollars: float = 0.0
    r_multiple: float = 0.0


def load_bars() -> dict[str, pd.DataFrame]:
    print(f"Loading bars from {BARS_DIR}...")
    bars: dict[str, pd.DataFrame] = {}
    files = sorted(BARS_DIR.glob("*.parquet"))
    t0 = time.time()
    for f in files:
        df = pd.read_parquet(f, columns=["date", "open", "high", "low", "close", "volume"])
        if df.empty or len(df) < MIN_HISTORY:
            continue
        df["date"] = pd.to_datetime(df["date"].astype(str))
        df = df.dropna().drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
        df.set_index("date", inplace=True)
        df["vol_avg50"] = df["volume"].rolling(50, min_periods=50).mean()
        bars[f.stem] = df
    print(f"Loaded {len(bars)} tickers in {time.time()-t0:.1f}s")
    return bars


def get_month_ends(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Last business day of each month between start and end."""
    return list(pd.date_range(start, end, freq="BME"))


def rank_top_momentum(bars: dict[str, pd.DataFrame], rank_date: pd.Timestamp, top_n: int) -> list[tuple[str, float]]:
    """Compute 12-1 momentum at rank_date for all eligible tickers, return top N."""
    candidates: list[tuple[str, float, float]] = []  # (ticker, mom, last_close)
    target_lookback = rank_date - pd.DateOffset(months=13)
    target_skip = rank_date - pd.DateOffset(months=1)

    for sym, df in bars.items():
        if rank_date not in df.index:
            # Use most recent date <= rank_date
            try:
                pos = df.index.get_indexer([rank_date], method="ffill")[0]
                if pos < 0:
                    continue
                actual_date = df.index[pos]
            except Exception:
                continue
        else:
            actual_date = rank_date
        idx_now = df.index.get_loc(actual_date)
        if idx_now < MIN_HISTORY:
            continue

        # Find the price 13 months ago and 1 month ago
        lookback_pos = df.index.get_indexer([target_lookback], method="ffill")[0]
        skip_pos = df.index.get_indexer([target_skip], method="ffill")[0]
        if lookback_pos < 0 or skip_pos < 0 or skip_pos <= lookback_pos:
            continue

        p_13mo = float(df.iloc[lookback_pos]["close"])
        p_1mo = float(df.iloc[skip_pos]["close"])
        if p_13mo <= 0:
            continue

        mom_12_1 = p_1mo / p_13mo - 1.0

        # Universe filter at rank_date
        last = df.iloc[idx_now]
        if not (MIN_PRICE <= last["close"] <= MAX_PRICE):
            continue
        if pd.isna(last.get("vol_avg50")) or last["vol_avg50"] < MIN_ADV:
            continue

        candidates.append((sym, mom_12_1, float(last["close"])))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [(s, m) for s, m, _ in candidates[:top_n]]


def mark_to_market(cash: float, open_pos: dict[str, Trade], bars: dict[str, pd.DataFrame], today: pd.Timestamp) -> float:
    total = cash
    for sym, trade in open_pos.items():
        if sym in bars and today in bars[sym].index:
            total += trade.shares * float(bars[sym].loc[today, "close"])
        else:
            total += trade.shares * trade.entry_price
    return total


def run_backtest(bars: dict[str, pd.DataFrame], spy: pd.DataFrame) -> tuple[list[Trade], pd.DataFrame]:
    print(f"\nBacktest CSM 12-1: {BACKTEST_START.date()} → {BACKTEST_END.date()}")
    print(f"  Top N = {TOP_N}, ${DOLLAR_PER_TRADE}/trade, stop {STOP_PCT:.0%}, target {TARGET_PCT:.0%}, time {TIME_STOP_DAYS}d\n")

    capital = INITIAL_CAPITAL
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    open_positions: dict[str, Trade] = {}
    closed_trades: list[Trade] = []

    month_ends = get_month_ends(BACKTEST_START, BACKTEST_END)
    next_rebalance_idx = 0
    pending_buys: list[tuple[str, float]] = []  # ticker, momentum — buy on next market open

    trading_days = pd.bdate_range(BACKTEST_START, BACKTEST_END)
    t0 = time.time()

    for day_idx, today_date in enumerate(trading_days):
        # 1. Process exits on open positions (intra-day check)
        to_close: list[str] = []
        for sym, trade in open_positions.items():
            if sym not in bars or today_date not in bars[sym].index:
                continue
            row = bars[sym].loc[today_date]
            held_days = (today_date - trade.entry_date).days

            if row["low"] <= trade.stop_price:
                exit_price = trade.stop_price * (1 - SLIPPAGE_EXIT)
                reason = "stop"
            elif row["high"] >= trade.target_price:
                exit_price = trade.target_price * (1 - SLIPPAGE_EXIT)
                reason = "target"
            elif held_days >= TIME_STOP_DAYS:
                exit_price = float(row["close"]) * (1 - SLIPPAGE_EXIT)
                reason = "time"
            else:
                continue

            trade.exit_date = today_date
            trade.exit_price = float(exit_price)
            trade.exit_reason = reason
            risk_per_share = trade.entry_price - trade.stop_price
            trade.pnl_dollars = (trade.exit_price - trade.entry_price) * trade.shares - 2 * trade.shares * COMMISSION_PER_SHARE
            trade.r_multiple = (trade.exit_price - trade.entry_price) / risk_per_share if risk_per_share > 0 else 0
            capital += trade.exit_price * trade.shares - trade.shares * COMMISSION_PER_SHARE
            closed_trades.append(trade)
            to_close.append(sym)
        for sym in to_close:
            del open_positions[sym]

        # 2. Process pending buys (queued from yesterday's monthly rebalance)
        for sym, mom in pending_buys[:]:
            if sym in open_positions:
                pending_buys.remove((sym, mom))
                continue
            if sym not in bars or today_date not in bars[sym].index:
                continue
            if len(open_positions) >= MAX_POSITIONS:
                break
            row = bars[sym].loc[today_date]
            entry = float(row["open"]) * (1 + SLIPPAGE_ENTRY)
            shares = int(DOLLAR_PER_TRADE / entry)
            if shares <= 0:
                pending_buys.remove((sym, mom))
                continue
            cost = shares * entry + shares * COMMISSION_PER_SHARE
            if cost > capital:
                break
            stop = entry * (1 - STOP_PCT)
            target = entry * (1 + TARGET_PCT)
            open_positions[sym] = Trade(
                symbol=sym, entry_date=today_date, entry_price=entry,
                stop_price=stop, target_price=target, shares=shares,
                momentum_12_1=mom,
            )
            capital -= cost
            pending_buys.remove((sym, mom))

        # 3. Monthly rebalance check
        if next_rebalance_idx < len(month_ends) and today_date >= month_ends[next_rebalance_idx]:
            top = rank_top_momentum(bars, today_date, TOP_N)
            # Queue buys for tomorrow open (skip any already held)
            for sym, mom in top:
                if sym not in open_positions and not any(p[0] == sym for p in pending_buys):
                    pending_buys.append((sym, mom))
            next_rebalance_idx += 1
            if len(closed_trades) > 0 and next_rebalance_idx % 12 == 0:
                eq = mark_to_market(capital, open_positions, bars, today_date)
                print(f"  [{today_date.date()}] equity=${eq:,.0f}, open={len(open_positions)}, closed={len(closed_trades)}, last 12mo top: {[s for s,m in top[:3]]}")

        equity_curve.append((today_date, mark_to_market(capital, open_positions, bars, today_date)))

    print(f"\nBacktest done in {time.time()-t0:.1f}s")
    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"]).set_index("date")
    return closed_trades, eq_df


def report(trades: list[Trade], equity: pd.DataFrame, spy: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("  RESULTS — Cross-Sectional Momentum (12-1) — 7%/21% R:R")
    print("=" * 60)
    if not trades:
        print("No trades.")
        return

    df = pd.DataFrame([{
        "symbol": t.symbol, "entry_date": t.entry_date, "exit_date": t.exit_date,
        "exit_reason": t.exit_reason, "r_multiple": t.r_multiple, "pnl": t.pnl_dollars,
        "held_days": (t.exit_date - t.entry_date).days if t.exit_date else 0,
        "momentum_12_1": t.momentum_12_1,
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
    print(f"Avg momentum at entry: {df['momentum_12_1'].mean():.1%}")

    print(f"\nExit reasons:")
    print(df["exit_reason"].value_counts().to_string())

    final_eq = equity["equity"].iloc[-1]
    total_return = final_eq / INITIAL_CAPITAL - 1
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (final_eq / INITIAL_CAPITAL) ** (1 / years) - 1 if years > 0 else 0
    print(f"\nFinal equity:    ${final_eq:,.0f}")
    print(f"Total return:    {total_return:+.1%}")
    print(f"CAGR:            {cagr:+.1%}")

    rolling_max = equity["equity"].cummax()
    dd = (equity["equity"] - rolling_max) / rolling_max
    print(f"Max drawdown:    {dd.min():.1%}")

    daily_ret = equity["equity"].pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    print(f"Sharpe (daily):  {sharpe:.2f}")

    # Benchmark vs SPY buy-and-hold
    spy_aligned = spy["close"].reindex(equity.index, method="ffill")
    if not spy_aligned.empty and not pd.isna(spy_aligned.iloc[0]):
        spy_return = spy_aligned.iloc[-1] / spy_aligned.iloc[0] - 1
        spy_cagr = (spy_aligned.iloc[-1] / spy_aligned.iloc[0]) ** (1 / years) - 1
        print(f"\nSPY benchmark:")
        print(f"  Total return:  {spy_return:+.1%}")
        print(f"  CAGR:          {spy_cagr:+.1%}")
        print(f"  vs strategy:   {(cagr - spy_cagr)*100:+.1f} pp/yr")

    print("\n" + "=" * 60)
    print("  GO / NO-GO")
    print("=" * 60)
    if expectancy > 0.30 and win_rate > 0.40:
        print(f"GO — expectancy {expectancy:+.2f}R, win rate {win_rate:.0%}. Strategy has edge.")
    elif expectancy > 0.10:
        print(f"MAYBE — expectancy {expectancy:+.2f}R is positive but thin. Tune or look elsewhere.")
    else:
        print(f"NO-GO — expectancy {expectancy:+.2f}R near zero. Strategy not viable.")


def main() -> None:
    bars = load_bars()
    spy = bars.get(SPY_TICKER)
    if spy is None:
        # Try direct load
        spy_path = BARS_DIR / "SPY.parquet"
        if spy_path.exists():
            df = pd.read_parquet(spy_path)
            df["date"] = pd.to_datetime(df["date"].astype(str))
            df.set_index("date", inplace=True)
            spy = df
            bars[SPY_TICKER] = df
        else:
            raise SystemExit("Need SPY for benchmark")

    trades, equity = run_backtest(bars, spy)
    report(trades, equity, spy)


if __name__ == "__main__":
    main()
