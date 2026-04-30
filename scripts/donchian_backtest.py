"""Donchian 20-day breakout (Turtle-style) backtest.

Strategy:
  - Entry signal: today's close > 20-day high (excluding today). Buy NEXT day at open.
  - SPY regime filter: only enter when SPY > 200d MA.
  - Position sizing: $1,000 fixed per trade.
  - Stops/targets:
      stop:    entry × 0.94  (6% below)
      target:  entry × 1.18  (18% above, 1:3 R:R)
      time:    90 days max
  - Max open positions: 10
  - Max new entries per week: 5

Universe filters:
  - Price between $10 and $200 at signal date
  - 50-day avg volume > 500k shares
  - At least 250 days of history (need 200d MA + 20d window)
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
MAX_ENTRIES_PER_WEEK = 5

# Donchian breakout
DONCHIAN_WINDOW = 20
SPY_MA_WINDOW = 200

# Stop/target/time
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
MIN_HISTORY = 250


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    breakout_high: float  # the 20-day high that was broken
    signal_close: float  # close on the signal day (one day before entry)
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
        # 20-day high *excluding today* — shift by 1 then take rolling max of last 20
        df["donchian20"] = df["high"].shift(1).rolling(DONCHIAN_WINDOW, min_periods=DONCHIAN_WINDOW).max()
        bars[f.stem] = df
    print(f"Loaded {len(bars)} tickers in {time.time()-t0:.1f}s")
    return bars


def prepare_spy(spy: pd.DataFrame) -> pd.DataFrame:
    """Add 200-day MA and regime flag to SPY."""
    spy = spy.copy()
    spy["ma200"] = spy["close"].rolling(SPY_MA_WINDOW, min_periods=SPY_MA_WINDOW).mean()
    spy["regime_bull"] = spy["close"] > spy["ma200"]
    return spy


def mark_to_market(cash: float, open_pos: dict[str, Trade], bars: dict[str, pd.DataFrame], today: pd.Timestamp) -> float:
    total = cash
    for sym, trade in open_pos.items():
        if sym in bars and today in bars[sym].index:
            total += trade.shares * float(bars[sym].loc[today, "close"])
        else:
            total += trade.shares * trade.entry_price
    return total


def find_breakouts_today(bars: dict[str, pd.DataFrame], today: pd.Timestamp, held_or_pending: set[str]) -> list[tuple[str, float, float]]:
    """Find tickers whose close today > 20-day high (excluding today).

    Returns list of (symbol, signal_close, breakout_high) sorted by relative breakout
    strength (most decisive first), filtered by universe rules.
    """
    candidates: list[tuple[str, float, float, float]] = []  # (sym, close, high20, strength)
    for sym, df in bars.items():
        if sym in held_or_pending:
            continue
        if today not in df.index:
            continue
        row = df.loc[today]
        close_t = float(row["close"])
        high20 = row.get("donchian20")
        if pd.isna(high20):
            continue
        if close_t <= float(high20):
            continue
        # Universe filters at signal date
        if not (MIN_PRICE <= close_t <= MAX_PRICE):
            continue
        adv50 = row.get("vol_avg50")
        if pd.isna(adv50) or adv50 < MIN_ADV:
            continue
        strength = (close_t - float(high20)) / float(high20)
        candidates.append((sym, close_t, float(high20), strength))

    # Most decisive breakout first
    candidates.sort(key=lambda x: x[3], reverse=True)
    return [(s, c, h) for s, c, h, _ in candidates]


def run_backtest(bars: dict[str, pd.DataFrame], spy: pd.DataFrame) -> tuple[list[Trade], pd.DataFrame]:
    print(f"\nBacktest Donchian {DONCHIAN_WINDOW}-day breakout: {BACKTEST_START.date()} → {BACKTEST_END.date()}")
    print(f"  Max pos = {MAX_POSITIONS}, ${DOLLAR_PER_TRADE}/trade, stop {STOP_PCT:.0%}, target {TARGET_PCT:.0%}, time {TIME_STOP_DAYS}d")
    print(f"  SPY regime filter: close > {SPY_MA_WINDOW}-day MA")
    print(f"  Max new entries/week: {MAX_ENTRIES_PER_WEEK}\n")

    capital = INITIAL_CAPITAL
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    open_positions: dict[str, Trade] = {}
    closed_trades: list[Trade] = []

    spy_ready = prepare_spy(spy)
    # Pending buys: list of (sym, signal_close, breakout_high) — bought at NEXT day open
    pending_buys: list[tuple[str, float, float]] = []
    entries_this_week: dict[pd.Timestamp, int] = {}  # iso week start -> count

    trading_days = pd.bdate_range(BACKTEST_START, BACKTEST_END)
    t0 = time.time()
    last_year_logged = None

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

        # 2. Process pending buys (queued from yesterday's signals)
        # Buy at TODAY's open, in order of breakout strength (already sorted).
        for sym, signal_close, breakout_high in pending_buys[:]:
            if sym in open_positions:
                pending_buys.remove((sym, signal_close, breakout_high))
                continue
            if sym not in bars or today_date not in bars[sym].index:
                pending_buys.remove((sym, signal_close, breakout_high))
                continue
            if len(open_positions) >= MAX_POSITIONS:
                break
            # Per-week entry cap
            week_start = today_date.to_period("W-FRI").start_time.normalize()
            if entries_this_week.get(week_start, 0) >= MAX_ENTRIES_PER_WEEK:
                break

            row = bars[sym].loc[today_date]
            entry = float(row["open"]) * (1 + SLIPPAGE_ENTRY)
            shares = int(DOLLAR_PER_TRADE / entry)
            if shares <= 0:
                pending_buys.remove((sym, signal_close, breakout_high))
                continue
            cost = shares * entry + shares * COMMISSION_PER_SHARE
            if cost > capital:
                break
            stop = entry * (1 - STOP_PCT)
            target = entry * (1 + TARGET_PCT)
            open_positions[sym] = Trade(
                symbol=sym, entry_date=today_date, entry_price=entry,
                stop_price=stop, target_price=target, shares=shares,
                breakout_high=breakout_high, signal_close=signal_close,
            )
            capital -= cost
            entries_this_week[week_start] = entries_this_week.get(week_start, 0) + 1
            pending_buys.remove((sym, signal_close, breakout_high))

        # Drop any pending buys that were not filled today (signals are 1-day fresh)
        pending_buys = []

        # 3. Generate new signals for tomorrow's open
        # Check SPY regime: skip new entries if SPY not in bull regime
        spy_today = None
        if today_date in spy_ready.index:
            spy_today = spy_ready.loc[today_date]
        if spy_today is not None and bool(spy_today.get("regime_bull", False)):
            held_or_pending = set(open_positions.keys())
            breakouts = find_breakouts_today(bars, today_date, held_or_pending)
            # Pre-cap candidates so we don't carry hundreds in pending; week cap also enforced at fill time
            max_signals_today = MAX_POSITIONS - len(open_positions)
            if max_signals_today > 0:
                pending_buys = breakouts[:max_signals_today * 3]  # extra slack since not all will fill

        # 4. Equity tracking
        eq = mark_to_market(capital, open_positions, bars, today_date)
        equity_curve.append((today_date, eq))

        # Annual progress log
        if today_date.year != last_year_logged and today_date.month == 12 and today_date.day >= 28:
            print(f"  [{today_date.date()}] equity=${eq:,.0f}, open={len(open_positions)}, closed={len(closed_trades)}")
            last_year_logged = today_date.year

    print(f"\nBacktest done in {time.time()-t0:.1f}s")
    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"]).set_index("date")
    return closed_trades, eq_df


def report(trades: list[Trade], equity: pd.DataFrame, spy: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print(f"  RESULTS — Donchian {DONCHIAN_WINDOW}-day breakout (Turtle-style)")
    print("=" * 60)
    if not trades:
        print("No trades.")
        return

    df = pd.DataFrame([{
        "symbol": t.symbol, "entry_date": t.entry_date, "exit_date": t.exit_date,
        "exit_reason": t.exit_reason, "r_multiple": t.r_multiple, "pnl": t.pnl_dollars,
        "held_days": (t.exit_date - t.entry_date).days if t.exit_date else 0,
        "entry_price": t.entry_price, "breakout_high": t.breakout_high,
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
