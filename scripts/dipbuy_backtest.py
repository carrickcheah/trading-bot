"""Buy-the-Dip on Quality backtest.

Strategy idea: pullbacks in strong uptrends often resume the trend. Filter for
QUALITY (long-term momentum + healthy MA stack) then wait for a tactical
pullback before buying the bounce.

Universe filters (at signal date):
  - Price between $10 and $200
  - 50-day average volume > 500k shares
  - At least 250 trading days of history

Quality filter (ALL required):
  - close > 50d MA > 200d MA (clean uptrend stack)
  - 12-month return > +20% (top performer)
  - 50d MA rising over the last 30 days

Pullback signal (ALL required) on signal day:
  - Stock down >= 10% from its 252-day (52-week) high
  - 14-day RSI < 40 (oversold within the uptrend)
  - Today's close > today's open (start of bounce, not still falling)

Entry / sizing:
  - BUY at the next day's open with a fixed $1,000 per trade
  - Max 10 open positions, max 5 new entries per week

Exits (whichever first):
  - Stop:    entry x 0.94      (6% below entry)
  - Target:  entry x 1.18      (18% above, 1:3 R:R)
  - Time:    90 calendar days

Costs:
  - Slippage: 0.2% entry, 0.1% exit
  - Commission: $0.005 / share each side

Period: 2012-01-01 to 2026-04-29.
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

# Quality filter
MA_FAST_PERIOD = 50
MA_SLOW_PERIOD = 200
MA_RISING_LOOKBACK = 30  # 50d MA must be > the 50d MA from 30 trading days ago
MOMENTUM_LOOKBACK = 252  # 12 months of trading days
MOMENTUM_MIN = 0.20  # > +20% over the last 12 months

# Pullback signal
HIGH_LOOKBACK = 252  # 52-week high window (trading days)
PULLBACK_MIN = 0.10  # >= 10% off the 52w high
RSI_PERIOD = 14
RSI_ENTRY_THRESHOLD = 40.0  # 14-day RSI must be strictly below this

# Stops/target/time
STOP_PCT = 0.06
TARGET_PCT = 0.18
TIME_STOP_DAYS = 90

# Costs
SLIPPAGE_ENTRY = 0.002
SLIPPAGE_EXIT = 0.001
COMMISSION_PER_SHARE = 0.005

# Universe filters
MIN_PRICE = 10.0
MAX_PRICE = 200.0
MIN_ADV = 500_000
# Need 252 days for 52w high + 200d MA before the 30-day rising lookback. 250 is the
# absolute floor; we additionally guard with explicit position checks below.
MIN_HISTORY = 250


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    entry_rsi: float
    pullback_pct: float  # how far below 52w high at signal (positive number)
    momentum_12m: float  # 12-month return at signal
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    pnl_dollars: float = 0.0
    r_multiple: float = 0.0


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (EWM with alpha = 1/period)."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi


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

        # Pre-compute indicators once
        df["vol_avg50"] = df["volume"].rolling(50, min_periods=50).mean()
        df["ma50"] = df["close"].rolling(MA_FAST_PERIOD, min_periods=MA_FAST_PERIOD).mean()
        df["ma200"] = df["close"].rolling(MA_SLOW_PERIOD, min_periods=MA_SLOW_PERIOD).mean()
        df["high252"] = df["close"].rolling(HIGH_LOOKBACK, min_periods=HIGH_LOOKBACK).max()
        df["close_252_ago"] = df["close"].shift(MOMENTUM_LOOKBACK)
        df["ma50_30_ago"] = df["ma50"].shift(MA_RISING_LOOKBACK)
        df["rsi14"] = compute_rsi(df["close"], RSI_PERIOD)

        bars[f.stem] = df
    print(f"Loaded {len(bars)} tickers (skipped {skipped}) in {time.time()-t0:.1f}s")
    return bars


def find_signals(
    bars: dict[str, pd.DataFrame],
    today: pd.Timestamp,
    held_or_pending: set[str],
) -> list[tuple[str, float, float, float]]:
    """Scan all tickers for dipbuy quality+pullback signals firing today.

    Returns list of (symbol, entry_rsi, pullback_pct, momentum_12m) tuples
    sorted by entry_rsi ascending (most oversold first = best dip).
    """
    sigs: list[tuple[str, float, float, float]] = []

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
        close_today = float(row["close"])
        open_today = float(row["open"])
        if not (MIN_PRICE <= close_today <= MAX_PRICE):
            continue
        vol_avg = row.get("vol_avg50")
        if pd.isna(vol_avg) or vol_avg < MIN_ADV:
            continue

        # Quality stack: close > 50d MA > 200d MA
        ma50 = row.get("ma50")
        ma200 = row.get("ma200")
        if pd.isna(ma50) or pd.isna(ma200):
            continue
        if not (close_today > ma50 > ma200):
            continue

        # 12-month return > +20%
        c_12mo_ago = row.get("close_252_ago")
        if pd.isna(c_12mo_ago) or c_12mo_ago <= 0:
            continue
        mom_12m = close_today / float(c_12mo_ago) - 1.0
        if mom_12m <= MOMENTUM_MIN:
            continue

        # 50d MA rising over the last 30 days
        ma50_prev = row.get("ma50_30_ago")
        if pd.isna(ma50_prev) or ma50 <= ma50_prev:
            continue

        # Pullback >= 10% from 52w high
        high_52 = row.get("high252")
        if pd.isna(high_52) or high_52 <= 0:
            continue
        pullback_pct = 1.0 - close_today / float(high_52)
        if pullback_pct < PULLBACK_MIN:
            continue

        # 14-day RSI < 40
        rsi = row.get("rsi14")
        if pd.isna(rsi) or rsi >= RSI_ENTRY_THRESHOLD:
            continue

        # Today's close > today's open (start of bounce)
        if close_today <= open_today:
            continue

        sigs.append((sym, float(rsi), float(pullback_pct), float(mom_12m)))

    # Most oversold first (lowest RSI). Tie-break implicit by Python's stable sort.
    sigs.sort(key=lambda x: x[1])
    return sigs


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


def run_backtest(bars: dict[str, pd.DataFrame]) -> tuple[list[Trade], pd.DataFrame]:
    print(f"\nBacktest Buy-the-Dip on Quality: {BACKTEST_START.date()} → {BACKTEST_END.date()}")
    print(
        f"  quality: c>50dMA>200dMA, 12m>{MOMENTUM_MIN:.0%}, 50dMA rising;"
        f" pullback: -{PULLBACK_MIN:.0%} from 52w high, RSI{RSI_PERIOD}<{RSI_ENTRY_THRESHOLD:.0f},"
        f" close>open"
    )
    print(
        f"  ${DOLLAR_PER_TRADE:,.0f}/trade, stop {STOP_PCT:.0%}, target {TARGET_PCT:.0%},"
        f" time {TIME_STOP_DAYS}d, max positions {MAX_POSITIONS}, max entries/wk {MAX_ENTRIES_PER_WEEK}\n"
    )

    capital = INITIAL_CAPITAL
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    open_positions: dict[str, Trade] = {}
    closed_trades: list[Trade] = []
    pending_buys: list[tuple[str, float, float, float]] = []  # symbol, rsi, pullback, mom

    # Track entries per (iso_year, iso_week)
    entries_this_week: dict[tuple[int, int], int] = {}

    trading_days = pd.bdate_range(BACKTEST_START, BACKTEST_END)
    t0 = time.time()
    last_print_year = -1

    for today_date in trading_days:
        # 1. Process exits on open positions
        to_close: list[str] = []
        for sym, trade in open_positions.items():
            if sym not in bars or today_date not in bars[sym].index:
                continue
            row = bars[sym].loc[today_date]
            held_days = (today_date - trade.entry_date).days

            exit_price: float | None = None
            reason = ""

            # Stop checked first (conservative when both stop+target hit same bar)
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
            trade.pnl_dollars = (
                (trade.exit_price - trade.entry_price) * trade.shares
                - 2 * trade.shares * COMMISSION_PER_SHARE
            )
            trade.r_multiple = (
                (trade.exit_price - trade.entry_price) / risk_per_share if risk_per_share > 0 else 0
            )
            capital += trade.exit_price * trade.shares - trade.shares * COMMISSION_PER_SHARE
            closed_trades.append(trade)
            to_close.append(sym)
        for sym in to_close:
            del open_positions[sym]

        # 2. Process pending buys (queued from yesterday's signal scan)
        iso_year, iso_week, _ = today_date.isocalendar()
        wk_key = (iso_year, iso_week)
        for sig in pending_buys[:]:
            sym, rsi, pullback, mom = sig
            if sym in open_positions:
                pending_buys.remove(sig)
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
                pending_buys.remove(sig)
                continue
            cost = shares * entry + shares * COMMISSION_PER_SHARE
            if cost > capital:
                pending_buys.remove(sig)
                continue
            stop = entry * (1 - STOP_PCT)
            target = entry * (1 + TARGET_PCT)
            open_positions[sym] = Trade(
                symbol=sym,
                entry_date=today_date,
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                shares=shares,
                entry_rsi=rsi,
                pullback_pct=pullback,
                momentum_12m=mom,
            )
            capital -= cost
            entries_this_week[wk_key] = entries_this_week.get(wk_key, 0) + 1
            pending_buys.remove(sig)

        # Drop stale pending: dipbuy signals are valid only for the next open.
        pending_buys = []

        # 3. Today's scan generates signals for tomorrow's open
        held_or_pending = set(open_positions.keys())
        room = MAX_POSITIONS - len(open_positions)
        if room > 0:
            sigs = find_signals(bars, today_date, held_or_pending)
            # Queue up to MAX_ENTRIES_PER_WEEK candidates (entry caps re-enforced when filling).
            scan_limit = max(room, MAX_ENTRIES_PER_WEEK)
            for s in sigs[:scan_limit]:
                pending_buys.append(s)

        # Bookkeeping
        equity_curve.append(
            (today_date, mark_to_market(capital, open_positions, bars, today_date))
        )

        if today_date.year != last_print_year and today_date.month == 12:
            eq = equity_curve[-1][1]
            print(
                f"  [{today_date.date()}] equity=${eq:,.0f}, open={len(open_positions)},"
                f" closed={len(closed_trades)}"
            )
            last_print_year = today_date.year

    print(f"\nBacktest done in {time.time()-t0:.1f}s")
    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"]).set_index("date")
    return closed_trades, eq_df


def report(trades: list[Trade], equity: pd.DataFrame, spy: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("  RESULTS — Buy-the-Dip on Quality")
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
                "entry_rsi": t.entry_rsi,
                "pullback_pct": t.pullback_pct,
                "momentum_12m": t.momentum_12m,
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
    print(f"Win rate:        {win_rate:.1%}    <-- HIGHLIGHT")
    print(f"Avg win:         {avg_win_r:+.2f}R")
    print(f"Avg loss:        {avg_loss_r:+.2f}R")
    print(f"Expectancy:      {expectancy:+.2f}R per trade")
    print(f"Avg hold:        {df['held_days'].mean():.1f} days")
    print(f"Avg entry RSI:   {df['entry_rsi'].mean():.1f}")
    print(f"Avg pullback:    -{df['pullback_pct'].mean()*100:.1f}% from 52w high")
    print(f"Avg 12m mom at entry: {df['momentum_12m'].mean()*100:+.1f}%")

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
            raise SystemExit("Need SPY for benchmark")

    trades, equity = run_backtest(bars)
    report(trades, equity, spy)


if __name__ == "__main__":
    main()
