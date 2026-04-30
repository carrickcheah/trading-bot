"""Bollinger Band Mean Reversion (uptrend pullback) backtest.

Strategy:
  - Trend filter: close > 200-day MA (only buy uptrending stocks pulling back).
  - Bollinger Bands: 20-day MA +/- 2 standard deviations.
  - Entry signal: today's close < lower band (severe oversold). Buy NEXT day at open.
  - Position sizing: $1,000 fixed per trade.
  - Stops/targets:
      stop:    entry x 0.94  (6% below)
      target:  entry x 1.18  (18% above, 1:3 R:R)
      time:    90 days max
      aux:     close >= 20-day MA (mean-reversion target reached) -> exit at NEXT open
  - Max open positions: 10.
  - Max new entries per week: 5.

Universe filters (at signal date):
  - Price between $10 and $200
  - 50-day avg volume > 500k shares
  - At least 250 days of history (need 200d MA)
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

# Bollinger / trend
BB_WINDOW = 20
BB_STD_MULT = 2.0
TREND_MA_WINDOW = 200

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
MIN_HISTORY = 250  # need 200d MA


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    signal_close: float       # close on the signal day (one day before entry)
    lower_band: float         # the lower band value that was breached
    bb_mid: float             # 20-day MA at signal
    bb_distance: float        # (mid - close) / mid at signal — pullback depth
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
        # Bollinger Bands (20d MA +/- 2 std), and 200d trend MA
        df["bb_mid"] = df["close"].rolling(BB_WINDOW, min_periods=BB_WINDOW).mean()
        df["bb_std"] = df["close"].rolling(BB_WINDOW, min_periods=BB_WINDOW).std(ddof=0)
        df["bb_lower"] = df["bb_mid"] - BB_STD_MULT * df["bb_std"]
        df["bb_upper"] = df["bb_mid"] + BB_STD_MULT * df["bb_std"]
        df["ma200"] = df["close"].rolling(TREND_MA_WINDOW, min_periods=TREND_MA_WINDOW).mean()
        bars[f.stem] = df
    print(f"Loaded {len(bars)} tickers in {time.time()-t0:.1f}s")
    return bars


def mark_to_market(cash: float, open_pos: dict[str, Trade], bars: dict[str, pd.DataFrame], today: pd.Timestamp) -> float:
    total = cash
    for sym, trade in open_pos.items():
        if sym in bars and today in bars[sym].index:
            total += trade.shares * float(bars[sym].loc[today, "close"])
        else:
            total += trade.shares * trade.entry_price
    return total


def find_signals_today(bars: dict[str, pd.DataFrame], today: pd.Timestamp, held_or_pending: set[str]) -> list[tuple[str, float, float, float]]:
    """Find tickers whose close today is below the lower Bollinger band AND price is in uptrend
    (close > 200-day MA). Returns list of (symbol, signal_close, lower_band, bb_mid) sorted by
    pullback depth — deepest oversold first.
    """
    candidates: list[tuple[str, float, float, float, float]] = []  # (sym, close, lower, mid, depth)
    for sym, df in bars.items():
        if sym in held_or_pending:
            continue
        if today not in df.index:
            continue
        row = df.loc[today]
        close_t = float(row["close"])
        lower = row.get("bb_lower")
        mid = row.get("bb_mid")
        ma200 = row.get("ma200")
        if pd.isna(lower) or pd.isna(mid) or pd.isna(ma200):
            continue
        # Uptrend filter — only buy stocks above 200d MA
        if close_t <= float(ma200):
            continue
        # Severe oversold — close below lower band
        if close_t >= float(lower):
            continue
        # Universe filters at signal date
        if not (MIN_PRICE <= close_t <= MAX_PRICE):
            continue
        adv50 = row.get("vol_avg50")
        if pd.isna(adv50) or adv50 < MIN_ADV:
            continue
        # Pullback depth — how far below the 20d MA we are (positive number)
        mid_f = float(mid)
        if mid_f <= 0:
            continue
        depth = (mid_f - close_t) / mid_f
        candidates.append((sym, close_t, float(lower), mid_f, depth))

    # Deepest pullback first (biggest mean-reversion potential)
    candidates.sort(key=lambda x: x[4], reverse=True)
    return [(s, c, lo, mi) for s, c, lo, mi, _ in candidates]


def run_backtest(bars: dict[str, pd.DataFrame], spy: pd.DataFrame) -> tuple[list[Trade], pd.DataFrame]:
    print(f"\nBacktest Bollinger Band Mean Reversion: {BACKTEST_START.date()} -> {BACKTEST_END.date()}")
    print(f"  BB window={BB_WINDOW}, std={BB_STD_MULT}, trend MA={TREND_MA_WINDOW}")
    print(f"  Max pos = {MAX_POSITIONS}, ${DOLLAR_PER_TRADE}/trade, stop {STOP_PCT:.0%}, target {TARGET_PCT:.0%}, time {TIME_STOP_DAYS}d")
    print(f"  Aux exit: close >= 20d MA -> sell next open")
    print(f"  Max new entries/week: {MAX_ENTRIES_PER_WEEK}\n")

    capital = INITIAL_CAPITAL
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    open_positions: dict[str, Trade] = {}
    closed_trades: list[Trade] = []

    # pending_buys: list of (sym, signal_close, lower, mid) — bought at NEXT day open
    pending_buys: list[tuple[str, float, float, float]] = []
    # aux_exits: symbols flagged today for "close >= 20d MA" — exit at NEXT day open
    aux_exits: set[str] = set()
    entries_this_week: dict[pd.Timestamp, int] = {}  # iso week start -> count

    trading_days = pd.bdate_range(BACKTEST_START, BACKTEST_END)
    t0 = time.time()
    last_year_logged = None

    for day_idx, today_date in enumerate(trading_days):
        # 1. Process exits on open positions
        # First: aux exits queued from yesterday — sell at TODAY's open before any other check
        to_close: list[str] = []
        for sym in list(aux_exits):
            if sym not in open_positions:
                continue
            trade = open_positions[sym]
            if sym not in bars or today_date not in bars[sym].index:
                continue
            row = bars[sym].loc[today_date]
            exit_price = float(row["open"]) * (1 - SLIPPAGE_EXIT)
            trade.exit_date = today_date
            trade.exit_price = float(exit_price)
            trade.exit_reason = "aux_mean_revert"
            risk_per_share = trade.entry_price - trade.stop_price
            trade.pnl_dollars = (trade.exit_price - trade.entry_price) * trade.shares - 2 * trade.shares * COMMISSION_PER_SHARE
            trade.r_multiple = (trade.exit_price - trade.entry_price) / risk_per_share if risk_per_share > 0 else 0
            capital += trade.exit_price * trade.shares - trade.shares * COMMISSION_PER_SHARE
            closed_trades.append(trade)
            to_close.append(sym)
        for sym in to_close:
            del open_positions[sym]
        aux_exits.clear()

        # Then: stop / target / time exits intraday
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
            trade.pnl_dollars = (trade.exit_price - trade.entry_price) * trade.shares - 2 * trade.shares * COMMISSION_PER_SHARE
            trade.r_multiple = (trade.exit_price - trade.entry_price) / risk_per_share if risk_per_share > 0 else 0
            capital += trade.exit_price * trade.shares - trade.shares * COMMISSION_PER_SHARE
            closed_trades.append(trade)
            to_close.append(sym)
        for sym in to_close:
            del open_positions[sym]

        # 2. Process pending buys (queued from yesterday's signals).
        # Buy at TODAY's open, in order of pullback depth (already sorted).
        for sym, signal_close, lower_band, bb_mid_signal in pending_buys[:]:
            if sym in open_positions:
                pending_buys.remove((sym, signal_close, lower_band, bb_mid_signal))
                continue
            if sym not in bars or today_date not in bars[sym].index:
                pending_buys.remove((sym, signal_close, lower_band, bb_mid_signal))
                continue
            if len(open_positions) >= MAX_POSITIONS:
                break
            week_start = today_date.to_period("W-FRI").start_time.normalize()
            if entries_this_week.get(week_start, 0) >= MAX_ENTRIES_PER_WEEK:
                break

            row = bars[sym].loc[today_date]
            entry = float(row["open"]) * (1 + SLIPPAGE_ENTRY)
            shares = int(DOLLAR_PER_TRADE / entry)
            if shares <= 0:
                pending_buys.remove((sym, signal_close, lower_band, bb_mid_signal))
                continue
            cost = shares * entry + shares * COMMISSION_PER_SHARE
            if cost > capital:
                break
            stop = entry * (1 - STOP_PCT)
            target = entry * (1 + TARGET_PCT)
            mid_f = float(bb_mid_signal)
            depth = (mid_f - signal_close) / mid_f if mid_f > 0 else 0.0
            open_positions[sym] = Trade(
                symbol=sym, entry_date=today_date, entry_price=entry,
                stop_price=stop, target_price=target, shares=shares,
                signal_close=signal_close, lower_band=lower_band,
                bb_mid=mid_f, bb_distance=depth,
            )
            capital -= cost
            entries_this_week[week_start] = entries_this_week.get(week_start, 0) + 1
            pending_buys.remove((sym, signal_close, lower_band, bb_mid_signal))

        # Drop any pending buys not filled today (signals are 1-day fresh)
        pending_buys = []

        # 3. Generate new signals for TOMORROW's open
        held_or_pending = set(open_positions.keys())
        signals = find_signals_today(bars, today_date, held_or_pending)
        max_signals_today = MAX_POSITIONS - len(open_positions)
        if max_signals_today > 0 and signals:
            pending_buys = signals[:max_signals_today * 3]  # extra slack since not all will fill

        # 4. Flag aux-exit for next-day open: any open position whose close today >= its 20d MA today
        for sym, trade in open_positions.items():
            if sym not in bars or today_date not in bars[sym].index:
                continue
            row = bars[sym].loc[today_date]
            mid_today = row.get("bb_mid")
            if pd.isna(mid_today):
                continue
            if float(row["close"]) >= float(mid_today):
                aux_exits.add(sym)

        # 5. Equity tracking
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
    print(f"  RESULTS — Bollinger Band Mean Reversion ({BB_WINDOW}d, {BB_STD_MULT}std)")
    print("=" * 60)
    if not trades:
        print("No trades.")
        return

    df = pd.DataFrame([{
        "symbol": t.symbol, "entry_date": t.entry_date, "exit_date": t.exit_date,
        "exit_reason": t.exit_reason, "r_multiple": t.r_multiple, "pnl": t.pnl_dollars,
        "held_days": (t.exit_date - t.entry_date).days if t.exit_date else 0,
        "entry_price": t.entry_price, "lower_band": t.lower_band,
        "bb_mid": t.bb_mid, "bb_distance": t.bb_distance,
    } for t in trades])

    n = len(df)
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    win_rate = len(wins) / n
    avg_win_r = wins["r_multiple"].mean() if len(wins) else 0
    avg_loss_r = losses["r_multiple"].mean() if len(losses) else 0
    expectancy = win_rate * avg_win_r + (1 - win_rate) * avg_loss_r

    print(f"\nTotal trades:    {n}")
    print(f"WIN RATE:        {win_rate:.1%}   <===")
    print(f"Avg win:         {avg_win_r:+.2f}R")
    print(f"Avg loss:        {avg_loss_r:+.2f}R")
    print(f"Expectancy:      {expectancy:+.2f}R per trade")
    print(f"Avg hold:        {df['held_days'].mean():.0f} days")
    print(f"Avg pullback at signal: {df['bb_distance'].mean():.1%}")

    print(f"\nExit reasons:")
    print(df["exit_reason"].value_counts().to_string())

    # Per-exit-reason win-rate breakdown
    print(f"\nPer-exit-reason performance:")
    by_reason = df.groupby("exit_reason").agg(
        n=("pnl", "size"),
        win_rate=("pnl", lambda s: (s > 0).mean()),
        avg_r=("r_multiple", "mean"),
    )
    print(by_reason.to_string())

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
