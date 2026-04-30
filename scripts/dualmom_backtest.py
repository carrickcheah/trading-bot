"""Dual Momentum (Antonacci) backtest.

Strategy:
  Two-level momentum filter for higher consistency:
  1. ABSOLUTE momentum: stock's 12-month return must be > 0
     (else exclude — bear regime filter at the stock level)
  2. RELATIVE momentum: stock's 12-month return must beat SPY's 12-month return
     (else exclude — must outperform the index)
  3. Rank survivors by 12-month return desc, take top 10
  4. Buy at next month open

  - Each position has:
      stop:    entry × 0.94  (6% below)
      target:  entry × 1.18  (18% above)
      time:    90 days max
  - REGIME EXIT (cash defense):
      If SPY closes below its 200-day MA, exit ALL open positions
      at next market open (slippage 0.1%).  No new buys queued while
      SPY < 200d MA at the rebalance check.

Universe filters:
  - Price between $10 and $200 at signal date
  - 50-day avg volume > 500k shares
  - At least 270 trading days of history (need 12-month lookback)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
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
TOP_N = 10  # take top N by momentum each month after dual filter

# Stop / target / time
STOP_PCT = 0.06  # 6%
TARGET_PCT = 0.18  # 18% (1:3 R:R)
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

# Regime filter
SPY_MA_WINDOW = 200


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    momentum_12m: float  # 12-month return at signal
    spy_momentum_12m: float  # SPY's 12-month return at the signal date
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


def prepare_spy(spy: pd.DataFrame) -> pd.DataFrame:
    """Add the 200-day MA used for the regime filter."""
    spy = spy.copy()
    spy["ma200"] = spy["close"].rolling(SPY_MA_WINDOW, min_periods=SPY_MA_WINDOW).mean()
    return spy


def get_month_ends(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Last business day of each month between start and end."""
    return list(pd.date_range(start, end, freq="BME"))


def spy_momentum_12m(spy: pd.DataFrame, rank_date: pd.Timestamp) -> float | None:
    """SPY's 12-month return measured at rank_date (close 12 months ago → close at rank_date)."""
    if rank_date not in spy.index:
        try:
            pos = spy.index.get_indexer([rank_date], method="ffill")[0]
            if pos < 0:
                return None
            actual_date = spy.index[pos]
        except Exception:
            return None
    else:
        actual_date = rank_date
    target_lookback = actual_date - pd.DateOffset(months=12)
    lookback_pos = spy.index.get_indexer([target_lookback], method="ffill")[0]
    now_pos = spy.index.get_loc(actual_date)
    if lookback_pos < 0 or now_pos <= lookback_pos:
        return None
    p_12mo = float(spy.iloc[lookback_pos]["close"])
    p_now = float(spy.iloc[now_pos]["close"])
    if p_12mo <= 0:
        return None
    return p_now / p_12mo - 1.0


def spy_above_200ma(spy: pd.DataFrame, today: pd.Timestamp) -> bool | None:
    """True if SPY's close on `today` (or the latest prior session) is above its 200d MA.

    Returns None if not enough history yet."""
    if today in spy.index:
        actual = today
    else:
        pos = spy.index.get_indexer([today], method="ffill")[0]
        if pos < 0:
            return None
        actual = spy.index[pos]
    row = spy.loc[actual]
    if pd.isna(row.get("ma200")):
        return None
    return bool(row["close"] > row["ma200"])


def rank_dual_momentum(
    bars: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    rank_date: pd.Timestamp,
    top_n: int,
) -> tuple[list[tuple[str, float]], float]:
    """Apply the dual-momentum filter at `rank_date` and return up to `top_n`
    survivors, plus SPY's 12-month return at that date.

    1. Absolute momentum: 12-month return > 0
    2. Relative momentum: 12-month return > SPY's 12-month return
    3. Rank survivors by 12-month return descending, slice top_n
    """
    spy_mom = spy_momentum_12m(spy, rank_date)
    if spy_mom is None:
        return [], float("nan")

    candidates: list[tuple[str, float, float]] = []
    target_lookback = rank_date - pd.DateOffset(months=12)

    for sym, df in bars.items():
        if sym == SPY_TICKER:
            continue
        if rank_date not in df.index:
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

        # Find the close 12 months ago
        lookback_pos = df.index.get_indexer([target_lookback], method="ffill")[0]
        if lookback_pos < 0 or idx_now <= lookback_pos:
            continue

        p_12mo = float(df.iloc[lookback_pos]["close"])
        p_now = float(df.iloc[idx_now]["close"])
        if p_12mo <= 0:
            continue

        mom_12m = p_now / p_12mo - 1.0

        # Dual filter
        if mom_12m <= 0:  # absolute momentum
            continue
        if mom_12m <= spy_mom:  # relative momentum (must beat SPY)
            continue

        # Universe filter at rank_date
        last = df.iloc[idx_now]
        if not (MIN_PRICE <= last["close"] <= MAX_PRICE):
            continue
        if pd.isna(last.get("vol_avg50")) or last["vol_avg50"] < MIN_ADV:
            continue

        candidates.append((sym, mom_12m, float(last["close"])))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [(s, m) for s, m, _ in candidates[:top_n]], spy_mom


def mark_to_market(
    cash: float,
    open_pos: dict[str, Trade],
    bars: dict[str, pd.DataFrame],
    today: pd.Timestamp,
) -> float:
    total = cash
    for sym, trade in open_pos.items():
        if sym in bars and today in bars[sym].index:
            total += trade.shares * float(bars[sym].loc[today, "close"])
        else:
            total += trade.shares * trade.entry_price
    return total


def run_backtest(
    bars: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
) -> tuple[list[Trade], pd.DataFrame]:
    print(f"\nBacktest Dual Momentum: {BACKTEST_START.date()} → {BACKTEST_END.date()}")
    print(
        f"  Top N = {TOP_N}, ${DOLLAR_PER_TRADE}/trade, "
        f"stop {STOP_PCT:.0%}, target {TARGET_PCT:.0%}, time {TIME_STOP_DAYS}d"
    )
    print(f"  Regime exit: SPY < {SPY_MA_WINDOW}d MA → flatten all → cash\n")

    capital = INITIAL_CAPITAL
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    open_positions: dict[str, Trade] = {}
    closed_trades: list[Trade] = []

    month_ends = get_month_ends(BACKTEST_START, BACKTEST_END)
    next_rebalance_idx = 0
    pending_buys: list[tuple[str, float, float]] = []  # ticker, mom, spy_mom — buy on next open
    regime_exit_pending = False  # flatten all positions on next open

    trading_days = pd.bdate_range(BACKTEST_START, BACKTEST_END)
    t0 = time.time()
    last_regime_state: bool | None = None

    for day_idx, today_date in enumerate(trading_days):
        # 1. Regime-exit flatten (queued from yesterday's close)
        if regime_exit_pending and open_positions:
            to_close: list[str] = []
            for sym, trade in open_positions.items():
                if sym not in bars or today_date not in bars[sym].index:
                    continue
                row = bars[sym].loc[today_date]
                exit_price = float(row["open"]) * (1 - SLIPPAGE_EXIT)
                trade.exit_date = today_date
                trade.exit_price = float(exit_price)
                trade.exit_reason = "regime"
                risk_per_share = trade.entry_price - trade.stop_price
                trade.pnl_dollars = (
                    (trade.exit_price - trade.entry_price) * trade.shares
                    - 2 * trade.shares * COMMISSION_PER_SHARE
                )
                trade.r_multiple = (
                    (trade.exit_price - trade.entry_price) / risk_per_share
                    if risk_per_share > 0
                    else 0
                )
                capital += trade.exit_price * trade.shares - trade.shares * COMMISSION_PER_SHARE
                closed_trades.append(trade)
                to_close.append(sym)
            for sym in to_close:
                del open_positions[sym]
            regime_exit_pending = False
            # Drop any pending buys queued before the regime turned (cash defense)
            pending_buys.clear()

        # 2. Process exits on remaining open positions (intra-day stop/target/time)
        to_close = []
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
            trade.pnl_dollars = (
                (trade.exit_price - trade.entry_price) * trade.shares
                - 2 * trade.shares * COMMISSION_PER_SHARE
            )
            trade.r_multiple = (
                (trade.exit_price - trade.entry_price) / risk_per_share
                if risk_per_share > 0
                else 0
            )
            capital += trade.exit_price * trade.shares - trade.shares * COMMISSION_PER_SHARE
            closed_trades.append(trade)
            to_close.append(sym)
        for sym in to_close:
            del open_positions[sym]

        # 3. Process pending buys (queued from yesterday's monthly rebalance)
        for sym, mom, spy_mom in pending_buys[:]:
            if sym in open_positions:
                pending_buys.remove((sym, mom, spy_mom))
                continue
            if sym not in bars or today_date not in bars[sym].index:
                continue
            if len(open_positions) >= MAX_POSITIONS:
                break
            row = bars[sym].loc[today_date]
            entry = float(row["open"]) * (1 + SLIPPAGE_ENTRY)
            shares = int(DOLLAR_PER_TRADE / entry)
            if shares <= 0:
                pending_buys.remove((sym, mom, spy_mom))
                continue
            cost = shares * entry + shares * COMMISSION_PER_SHARE
            if cost > capital:
                break
            stop = entry * (1 - STOP_PCT)
            target = entry * (1 + TARGET_PCT)
            open_positions[sym] = Trade(
                symbol=sym,
                entry_date=today_date,
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                shares=shares,
                momentum_12m=mom,
                spy_momentum_12m=spy_mom,
            )
            capital -= cost
            pending_buys.remove((sym, mom, spy_mom))

        # 4. Monthly rebalance check (uses today's close — buys execute at next open)
        if next_rebalance_idx < len(month_ends) and today_date >= month_ends[next_rebalance_idx]:
            spy_ok = spy_above_200ma(spy, today_date)
            if spy_ok is False:
                # Regime is risk-off: flatten everything at next open, no new buys
                regime_exit_pending = bool(open_positions)
                pending_buys.clear()
                top: list[tuple[str, float]] = []
                spy_mom = spy_momentum_12m(spy, today_date) or float("nan")
                if last_regime_state is not False:
                    print(
                        f"  [{today_date.date()}] REGIME OFF — SPY < 200d MA, "
                        f"flattening {len(open_positions)} positions"
                    )
                last_regime_state = False
            else:
                top, spy_mom = rank_dual_momentum(bars, spy, today_date, TOP_N)
                # Queue buys for tomorrow open (skip any already held)
                for sym, mom in top:
                    if sym not in open_positions and not any(p[0] == sym for p in pending_buys):
                        pending_buys.append((sym, mom, spy_mom))
                if last_regime_state is False:
                    print(f"  [{today_date.date()}] REGIME ON — SPY > 200d MA, resuming")
                last_regime_state = True

            next_rebalance_idx += 1
            if len(closed_trades) > 0 and next_rebalance_idx % 12 == 0:
                eq = mark_to_market(capital, open_positions, bars, today_date)
                preview = [s for s, _ in top[:3]] if top else ["(none — risk off)"]
                print(
                    f"  [{today_date.date()}] equity=${eq:,.0f}, "
                    f"open={len(open_positions)}, closed={len(closed_trades)}, "
                    f"spy_mom={spy_mom:+.1%}, top: {preview}"
                )

        equity_curve.append((today_date, mark_to_market(capital, open_positions, bars, today_date)))

    print(f"\nBacktest done in {time.time()-t0:.1f}s")
    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"]).set_index("date")
    return closed_trades, eq_df


def report(trades: list[Trade], equity: pd.DataFrame, spy: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("  RESULTS — Dual Momentum (Antonacci)")
    print("=" * 60)
    if not trades:
        print("No trades.")
        return

    df = pd.DataFrame(
        [
            {
                "symbol": t.symbol,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "exit_reason": t.exit_reason,
                "r_multiple": t.r_multiple,
                "pnl": t.pnl_dollars,
                "held_days": (t.exit_date - t.entry_date).days if t.exit_date else 0,
                "momentum_12m": t.momentum_12m,
                "spy_momentum_12m": t.spy_momentum_12m,
            }
            for t in trades
        ]
    )

    n = len(df)
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    win_rate = len(wins) / n
    avg_win_r = wins["r_multiple"].mean() if len(wins) else 0
    avg_loss_r = losses["r_multiple"].mean() if len(losses) else 0
    expectancy = win_rate * avg_win_r + (1 - win_rate) * avg_loss_r

    print(f"\nTotal trades:    {n}")
    print(f"Win rate:        {win_rate:.1%}   <<< highlight")
    print(f"Avg win:         {avg_win_r:+.2f}R")
    print(f"Avg loss:        {avg_loss_r:+.2f}R")
    print(f"Expectancy:      {expectancy:+.2f}R per trade")
    print(f"Avg hold:        {df['held_days'].mean():.0f} days")
    print(f"Avg momentum at entry: {df['momentum_12m'].mean():.1%}")
    print(f"Avg SPY momentum at entry: {df['spy_momentum_12m'].mean():.1%}")

    print("\nExit reasons:")
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
        print("\nSPY benchmark:")
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
        spy_path = BARS_DIR / "SPY.parquet"
        if spy_path.exists():
            df = pd.read_parquet(spy_path)
            df["date"] = pd.to_datetime(df["date"].astype(str))
            df.set_index("date", inplace=True)
            spy = df
            bars[SPY_TICKER] = df
        else:
            raise SystemExit("Need SPY for benchmark / regime filter")

    spy = prepare_spy(spy)
    bars[SPY_TICKER] = spy  # ensure mark-to-market sees the same frame

    trades, equity = run_backtest(bars, spy)
    report(trades, equity, spy)


if __name__ == "__main__":
    main()
