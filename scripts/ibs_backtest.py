"""Internal Bar Strength (IBS) mean reversion backtest.

IBS = (close - low) / (high - low). Range 0-1. Low IBS = closed near day's low.
Academic literature reports very high win rates for IBS-based mean reversion.

Strategy:
  - Universe: price $10-$200, 50d ADV > 500k, >=250 trading days history
  - Trend filter: close > 200-day MA (only buy strong stocks pulling back)
  - Entry signal:
      IBS < 0.10                          (closed in bottom 10% of day's range)
      AND prior close > prior open        (no extended downtrend; today is the dip)
  - Buy NEXT day at open at $1,000 fixed per trade
  - Exit (whichever first):
      stop:    entry x 0.94               (6% below)
      target:  entry x 1.18               (18% above, 1:3 R:R)
      aux:     IBS > 0.7 OR close > 5d MA (mean-reversion accomplished) -> exit at next open
      time:    90 days max
  - Max 10 open positions, max 5 new entries per week

Costs:
  - Slippage: 0.2% entry, 0.1% exit
  - Commission: $0.005/share
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

# Signal
IBS_ENTRY_THRESHOLD = 0.10  # buy when IBS < 0.10 (closed in bottom 10%)
IBS_EXIT_THRESHOLD = 0.70  # aux exit when IBS > 0.70
SHORT_MA_PERIOD = 5  # aux exit when close > 5d MA
TREND_MA_PERIOD = 200  # close must be > 200d MA

# Stops/target/time
STOP_PCT = 0.06
TARGET_PCT = 0.18
TIME_STOP_DAYS = 90

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
    entry_ibs: float
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    pnl_dollars: float = 0.0
    r_multiple: float = 0.0


def compute_ibs(df: pd.DataFrame) -> pd.Series:
    """IBS = (close - low) / (high - low). When high == low, define IBS = 0.5 (neutral)."""
    rng = df["high"] - df["low"]
    ibs = (df["close"] - df["low"]) / rng
    # Where high == low (zero range), set IBS = 0.5
    ibs = ibs.where(rng > 0, 0.5)
    # Clip to [0, 1] in case of any float noise (open/close outside H/L should not happen but just in case)
    return ibs.clip(0.0, 1.0)


def load_bars() -> dict[str, pd.DataFrame]:
    print(f"Loading bars from {BARS_DIR}...")
    bars: dict[str, pd.DataFrame] = {}
    files = sorted(BARS_DIR.glob("*.parquet"))
    t0 = time.time()
    skipped = 0
    for f in files:
        try:
            df = pd.read_parquet(f, columns=["date", "open", "high", "low", "close", "volume"])
        except Exception:
            skipped += 1
            continue
        if df.empty or len(df) < MIN_HISTORY:
            skipped += 1
            continue
        df["date"] = pd.to_datetime(df["date"].astype(str))
        df = df.dropna().drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
        if len(df) < MIN_HISTORY:
            skipped += 1
            continue
        df.set_index("date", inplace=True)
        # Pre-compute indicators
        df["vol_avg50"] = df["volume"].rolling(50, min_periods=50).mean()
        df["ma200"] = df["close"].rolling(TREND_MA_PERIOD, min_periods=TREND_MA_PERIOD).mean()
        df["ma5"] = df["close"].rolling(SHORT_MA_PERIOD, min_periods=SHORT_MA_PERIOD).mean()
        df["ibs"] = compute_ibs(df)
        # prior-day session bias (close > open) for entry filter
        df["prior_up"] = (df["close"].shift(1) > df["open"].shift(1)).astype("Int8")
        bars[f.stem] = df
    print(f"Loaded {len(bars)} tickers (skipped {skipped}) in {time.time()-t0:.1f}s")
    return bars


def find_signals(bars: dict[str, pd.DataFrame], today: pd.Timestamp, held_or_pending: set[str]) -> list[tuple[str, float]]:
    """Scan all tickers for those triggering an IBS<0.10 signal today, in an uptrend, prior up day."""
    sigs: list[tuple[str, float]] = []
    for sym, df in bars.items():
        if sym in held_or_pending:
            continue
        if today not in df.index:
            continue
        idx_pos = df.index.get_loc(today)
        if idx_pos < MIN_HISTORY:
            continue
        row = df.iloc[idx_pos]

        # Universe filter
        if not (MIN_PRICE <= row["close"] <= MAX_PRICE):
            continue
        if pd.isna(row.get("vol_avg50")) or row["vol_avg50"] < MIN_ADV:
            continue

        # Trend filter
        ma200 = row.get("ma200")
        if pd.isna(ma200) or row["close"] <= ma200:
            continue

        # Entry signal: IBS < threshold AND prior close > prior open
        ibs = row.get("ibs")
        if pd.isna(ibs) or ibs >= IBS_ENTRY_THRESHOLD:
            continue
        prior_up = row.get("prior_up")
        if pd.isna(prior_up) or int(prior_up) != 1:
            continue

        sigs.append((sym, float(ibs)))

    # Lower IBS = closer to day's low = stronger mean-reversion candidate. Sort ascending.
    sigs.sort(key=lambda x: x[1])
    return sigs


def mark_to_market(cash: float, open_pos: dict[str, Trade], bars: dict[str, pd.DataFrame], today: pd.Timestamp) -> float:
    total = cash
    for sym, trade in open_pos.items():
        if sym in bars and today in bars[sym].index:
            total += trade.shares * float(bars[sym].loc[today, "close"])
        else:
            total += trade.shares * trade.entry_price
    return total


def run_backtest(bars: dict[str, pd.DataFrame]) -> tuple[list[Trade], pd.DataFrame]:
    print(f"\nBacktest IBS mean reversion: {BACKTEST_START.date()} -> {BACKTEST_END.date()}")
    print(f"  IBS<{IBS_ENTRY_THRESHOLD} + prior up day, MA{TREND_MA_PERIOD}, ${DOLLAR_PER_TRADE}/trade, "
          f"stop {STOP_PCT:.0%}, target {TARGET_PCT:.0%}, time {TIME_STOP_DAYS}d")
    print(f"  aux exit: IBS>{IBS_EXIT_THRESHOLD} OR close>MA{SHORT_MA_PERIOD}")
    print(f"  max positions {MAX_POSITIONS}, max entries/week {MAX_ENTRIES_PER_WEEK}\n")

    capital = INITIAL_CAPITAL
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    open_positions: dict[str, Trade] = {}
    closed_trades: list[Trade] = []
    pending_buys: list[tuple[str, float]] = []  # (ticker, entry_ibs) -> buy on next market open
    # Aux exits queued from yesterday's close (exit at next open):
    pending_aux_exits: set[str] = set()

    # Track entries per (year, isoweek)
    entries_this_week: dict[tuple[int, int], int] = {}

    trading_days = pd.bdate_range(BACKTEST_START, BACKTEST_END)
    t0 = time.time()
    last_print_year = -1

    for day_idx, today_date in enumerate(trading_days):
        # 1. Process aux-exits queued from yesterday (exit at today's open)
        to_close: list[str] = []
        for sym in list(pending_aux_exits):
            if sym not in open_positions:
                pending_aux_exits.discard(sym)
                continue
            if sym not in bars or today_date not in bars[sym].index:
                continue
            trade = open_positions[sym]
            row = bars[sym].loc[today_date]
            # If gap-down hits stop on open, prefer stop semantics over aux-exit
            open_today = float(row["open"])
            # Use stop_price as worst case if open <= stop
            if open_today <= trade.stop_price:
                exit_price = trade.stop_price * (1 - SLIPPAGE_EXIT)
                reason = "stop"
            elif open_today >= trade.target_price:
                exit_price = trade.target_price * (1 - SLIPPAGE_EXIT)
                reason = "target"
            else:
                exit_price = open_today * (1 - SLIPPAGE_EXIT)
                reason = "aux"

            trade.exit_date = today_date
            trade.exit_price = float(exit_price)
            trade.exit_reason = reason
            risk_per_share = trade.entry_price - trade.stop_price
            trade.pnl_dollars = (trade.exit_price - trade.entry_price) * trade.shares - 2 * trade.shares * COMMISSION_PER_SHARE
            trade.r_multiple = (trade.exit_price - trade.entry_price) / risk_per_share if risk_per_share > 0 else 0
            capital += trade.exit_price * trade.shares - trade.shares * COMMISSION_PER_SHARE
            closed_trades.append(trade)
            to_close.append(sym)
            pending_aux_exits.discard(sym)

        # 2. Intraday stop/target/time exits on remaining positions
        for sym, trade in open_positions.items():
            if sym in to_close:
                continue  # already exited via aux
            if sym not in bars or today_date not in bars[sym].index:
                continue
            row = bars[sym].loc[today_date]
            held_days = (today_date - trade.entry_date).days

            exit_price = None
            reason = ""

            if row["low"] <= trade.stop_price:
                exit_price = trade.stop_price * (1 - SLIPPAGE_EXIT)
                reason = "stop"
            elif row["high"] >= trade.target_price:
                exit_price = trade.target_price * (1 - SLIPPAGE_EXIT)
                reason = "target"
            elif held_days >= TIME_STOP_DAYS:
                exit_price = float(row["close"]) * (1 - SLIPPAGE_EXIT)
                reason = "time"

            if exit_price is None:
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
            open_positions.pop(sym, None)

        # 3. Process pending buys (queued from yesterday's signal scan)
        iso_year, iso_week, _ = today_date.isocalendar()
        wk_key = (iso_year, iso_week)
        for sym, ent_ibs in pending_buys[:]:
            if sym in open_positions:
                pending_buys.remove((sym, ent_ibs))
                continue
            if sym not in bars or today_date not in bars[sym].index:
                continue
            if len(open_positions) >= MAX_POSITIONS:
                break
            if entries_this_week.get(wk_key, 0) >= MAX_ENTRIES_PER_WEEK:
                break
            row = bars[sym].loc[today_date]
            entry = float(row["open"]) * (1 + SLIPPAGE_ENTRY)
            shares = int(DOLLAR_PER_TRADE / entry)
            if shares <= 0:
                pending_buys.remove((sym, ent_ibs))
                continue
            cost = shares * entry + shares * COMMISSION_PER_SHARE
            if cost > capital:
                pending_buys.remove((sym, ent_ibs))
                continue
            stop = entry * (1 - STOP_PCT)
            target = entry * (1 + TARGET_PCT)
            open_positions[sym] = Trade(
                symbol=sym, entry_date=today_date, entry_price=entry,
                stop_price=stop, target_price=target, shares=shares,
                entry_ibs=ent_ibs,
            )
            capital -= cost
            entries_this_week[wk_key] = entries_this_week.get(wk_key, 0) + 1
            pending_buys.remove((sym, ent_ibs))

        # Stale pending buys (not filled today): drop. Signal stale after one open.
        pending_buys = []

        # 4. Today's close-of-day work:
        #    a) flag aux-exits for tomorrow's open
        #    b) generate IBS<0.10 signals for tomorrow's open
        for sym, trade in open_positions.items():
            if sym not in bars or today_date not in bars[sym].index:
                continue
            held_days = (today_date - trade.entry_date).days
            if held_days < 1:
                continue  # don't aux-exit same day (already exited above won't appear)
            row = bars[sym].loc[today_date]
            ibs_t = row.get("ibs")
            ma5 = row.get("ma5")
            close_t = float(row["close"])
            ibs_hit = (not pd.isna(ibs_t)) and float(ibs_t) > IBS_EXIT_THRESHOLD
            ma_hit = (not pd.isna(ma5)) and close_t > float(ma5)
            if ibs_hit or ma_hit:
                pending_aux_exits.add(sym)

        held_or_pending = set(open_positions.keys())
        room = MAX_POSITIONS - len(open_positions)
        scan_limit = max(room, MAX_ENTRIES_PER_WEEK)
        if room > 0:
            sigs = find_signals(bars, today_date, held_or_pending)
            for sym, ibs in sigs[: max(scan_limit, 0)]:
                pending_buys.append((sym, ibs))

        # Bookkeeping
        equity_curve.append((today_date, mark_to_market(capital, open_positions, bars, today_date)))

        if today_date.year != last_print_year and today_date.month == 12:
            eq = equity_curve[-1][1]
            print(f"  [{today_date.date()}] equity=${eq:,.0f}, open={len(open_positions)}, closed={len(closed_trades)}")
            last_print_year = today_date.year

    print(f"\nBacktest done in {time.time()-t0:.1f}s")
    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"]).set_index("date")
    return closed_trades, eq_df


def report(trades: list[Trade], equity: pd.DataFrame, spy: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("  RESULTS - IBS mean reversion")
    print("=" * 60)
    if not trades:
        print("No trades.")
        return

    df = pd.DataFrame([{
        "symbol": t.symbol, "entry_date": t.entry_date, "exit_date": t.exit_date,
        "exit_reason": t.exit_reason, "r_multiple": t.r_multiple, "pnl": t.pnl_dollars,
        "held_days": (t.exit_date - t.entry_date).days if t.exit_date else 0,
        "entry_ibs": t.entry_ibs,
    } for t in trades])

    n = len(df)
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    win_rate = len(wins) / n
    avg_win_r = wins["r_multiple"].mean() if len(wins) else 0
    avg_loss_r = losses["r_multiple"].mean() if len(losses) else 0
    expectancy = win_rate * avg_win_r + (1 - win_rate) * avg_loss_r

    print(f"\nTotal trades:    {n}")
    print(f"** WIN RATE:     {win_rate:.1%} **")
    print(f"Avg win:         {avg_win_r:+.2f}R")
    print(f"Avg loss:        {avg_loss_r:+.2f}R")
    print(f"Expectancy:      {expectancy:+.2f}R per trade")
    print(f"Avg hold:        {df['held_days'].mean():.1f} days")
    print(f"Avg entry IBS:   {df['entry_ibs'].mean():.3f}")

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
        print(f"GO - expectancy {expectancy:+.2f}R, win rate {win_rate:.0%}. Strategy has edge.")
    elif expectancy > 0.10:
        print(f"MAYBE - expectancy {expectancy:+.2f}R is positive but thin. Tune or look elsewhere.")
    else:
        print(f"NO-GO - expectancy {expectancy:+.2f}R near zero. Strategy not viable.")


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

    trades, equity = run_backtest(bars)
    report(trades, equity, spy)


if __name__ == "__main__":
    main()
