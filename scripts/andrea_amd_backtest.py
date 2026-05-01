"""Andrea's RSI + Support + Bullish Candle backtest — AMD ONLY (single-ticker variant).

Single-ticker adaptation of `andrea_backtest.py`. All universe filters stripped
(price band, ADV gate, max-positions, max-entries-per-week) since we trade one
name. One open AMD position at a time, no stacking.

Entry signal (ALL required) on signal day:
  - 14-day RSI < 30 (oversold)
  - Today's low <= 1.02 * (lowest low over the past 20 days)
  - Today's close > today's open (bullish reversal candle)
  - Today's range (high - low) > 0.5 * ATR(14)

Entry / sizing:
  - BUY at the next day's open with a fixed $1,000 per trade
  - One open position max (no stacking)

Exits (whichever first):
  - Stop:    close <= entry * 0.94  → exit at NEXT open
  - Target:  high  >= entry * 1.18  → exit at target intraday
  - Time:    90 calendar days       → exit at close

Costs:
  - Slippage: 0.2% entry, 0.1% exit
  - Commission: $0.005 / share each side

Period: 2012-01-01 to 2026-04-29
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# ---- config ----
BARS_PATH = Path("/data/bars/AMD.parquet")
SYMBOL = "AMD"

BACKTEST_START = pd.Timestamp("2012-01-01")
BACKTEST_END = pd.Timestamp("2026-04-29")
INITIAL_CAPITAL = 10_000.0
DOLLAR_PER_TRADE = 1_000.0

RSI_PERIOD = 14
RSI_ENTRY_THRESHOLD = 30.0
SUPPORT_LOOKBACK = 20
SUPPORT_TOLERANCE = 1.02
ATR_PERIOD = 14
RANGE_VS_ATR_MIN = 0.5

STOP_PCT = 0.06
TARGET_PCT = 0.18
TIME_STOP_DAYS = 90

SLIPPAGE_ENTRY = 0.002
SLIPPAGE_EXIT = 0.001
COMMISSION_PER_SHARE = 0.005


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    entry_rsi: float
    entry_atr: float
    range_atr_ratio: float
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    pnl_dollars: float = 0.0
    r_multiple: float = 0.0


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (EWM, alpha=1/period)."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's ATR (EWM of True Range)."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def load_amd() -> pd.DataFrame:
    print(f"Loading {SYMBOL} bars from {BARS_PATH}...")
    df = pd.read_parquet(
        BARS_PATH, columns=["date", "open", "high", "low", "close", "volume"]
    )
    df["date"] = pd.to_datetime(df["date"].astype(str))
    df = (
        df.dropna()
        .drop_duplicates(subset="date")
        .sort_values("date")
        .reset_index(drop=True)
        .set_index("date")
    )

    df["rsi14"] = compute_rsi(df["close"], RSI_PERIOD)
    df["atr14"] = compute_atr(df["high"], df["low"], df["close"], ATR_PERIOD)
    df["low20"] = df["low"].rolling(SUPPORT_LOOKBACK, min_periods=SUPPORT_LOOKBACK).min()

    print(
        f"  loaded {len(df)} bars from {df.index[0].date()} to {df.index[-1].date()}"
    )
    return df


def is_signal(row: pd.Series) -> tuple[bool, float, float, float]:
    """Return (fired, rsi, atr, range/atr) for a single bar."""
    close = float(row["close"])
    open_ = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    rsi = row.get("rsi14")
    atr = row.get("atr14")
    low20 = row.get("low20")

    if pd.isna(rsi) or pd.isna(atr) or pd.isna(low20):
        return False, 0.0, 0.0, 0.0
    if rsi >= RSI_ENTRY_THRESHOLD:
        return False, float(rsi), float(atr), 0.0
    if low20 <= 0 or low > SUPPORT_TOLERANCE * float(low20):
        return False, float(rsi), float(atr), 0.0
    if close <= open_:
        return False, float(rsi), float(atr), 0.0
    if atr <= 0:
        return False, float(rsi), float(atr), 0.0
    range_today = high - low
    if range_today <= RANGE_VS_ATR_MIN * float(atr):
        return False, float(rsi), float(atr), float(range_today / float(atr))
    return True, float(rsi), float(atr), float(range_today / float(atr))


def run_backtest(df: pd.DataFrame) -> tuple[list[Trade], pd.DataFrame]:
    print(f"\nBacktest Andrea on {SYMBOL}: {BACKTEST_START.date()} -> {BACKTEST_END.date()}")
    print(
        f"  signal: RSI{RSI_PERIOD}<{RSI_ENTRY_THRESHOLD:.0f},"
        f" low<={SUPPORT_TOLERANCE:.2f}*low{SUPPORT_LOOKBACK},"
        f" close>open, range>{RANGE_VS_ATR_MIN}*ATR{ATR_PERIOD}"
    )
    print(
        f"  ${DOLLAR_PER_TRADE:,.0f}/trade, stop -{STOP_PCT:.0%},"
        f" target +{TARGET_PCT:.0%}, time {TIME_STOP_DAYS}d, 1 position max\n"
    )

    capital = INITIAL_CAPITAL
    closed: list[Trade] = []
    open_trade: Trade | None = None
    pending_buy: bool = False
    equity_curve: list[tuple[pd.Timestamp, float]] = []

    in_window = df[(df.index >= BACKTEST_START) & (df.index <= BACKTEST_END)]
    dates = list(in_window.index)
    t0 = time.time()
    last_print_year = -1

    for i, today in enumerate(dates):
        row = in_window.iloc[i]
        close_today = float(row["close"])
        high_today = float(row["high"])
        open_today = float(row["open"])

        # ---- 1. Process exits ----
        if open_trade is not None:
            held_days = (today - open_trade.entry_date).days
            exit_price: float | None = None
            reason = ""

            if open_trade.exit_reason == "stop_pending":
                # stop armed yesterday -> exit at today's open
                exit_price = open_today * (1 - SLIPPAGE_EXIT)
                reason = "stop"
            elif high_today >= open_trade.target_price:
                exit_price = open_trade.target_price * (1 - SLIPPAGE_EXIT)
                reason = "target"
            elif close_today <= open_trade.stop_price:
                # arm stop, exit tomorrow's open
                open_trade.exit_reason = "stop_pending"
            elif held_days >= TIME_STOP_DAYS:
                exit_price = close_today * (1 - SLIPPAGE_EXIT)
                reason = "time"

            if exit_price is not None:
                open_trade.exit_date = today
                open_trade.exit_price = float(exit_price)
                open_trade.exit_reason = reason
                risk_per_share = open_trade.entry_price - open_trade.stop_price
                open_trade.pnl_dollars = (
                    (open_trade.exit_price - open_trade.entry_price) * open_trade.shares
                    - 2 * open_trade.shares * COMMISSION_PER_SHARE
                )
                open_trade.r_multiple = (
                    (open_trade.exit_price - open_trade.entry_price) / risk_per_share
                    if risk_per_share > 0
                    else 0.0
                )
                capital += (
                    open_trade.exit_price * open_trade.shares
                    - open_trade.shares * COMMISSION_PER_SHARE
                )
                closed.append(open_trade)
                open_trade = None

        # ---- 2. Process pending buy from yesterday ----
        if pending_buy and open_trade is None:
            entry = open_today * (1 + SLIPPAGE_ENTRY)
            shares = int(DOLLAR_PER_TRADE / entry)
            if shares > 0:
                cost = shares * entry + shares * COMMISSION_PER_SHARE
                if cost <= capital:
                    sig_row = in_window.iloc[i - 1]  # yesterday: signal day
                    fired, rsi_y, atr_y, ratio_y = is_signal(sig_row)
                    open_trade = Trade(
                        symbol=SYMBOL,
                        entry_date=today,
                        entry_price=entry,
                        stop_price=entry * (1 - STOP_PCT),
                        target_price=entry * (1 + TARGET_PCT),
                        shares=shares,
                        entry_rsi=rsi_y,
                        entry_atr=atr_y,
                        range_atr_ratio=ratio_y,
                    )
                    capital -= cost
        pending_buy = False

        # ---- 3. Generate today's signal for tomorrow's open ----
        if open_trade is None:
            fired, _rsi, _atr, _ratio = is_signal(row)
            if fired:
                pending_buy = True

        # ---- 4. Mark to market ----
        eq = capital
        if open_trade is not None:
            eq += open_trade.shares * close_today
        equity_curve.append((today, eq))

        if today.year != last_print_year and today.month == 12:
            print(
                f"  [{today.date()}] equity=${eq:,.0f} closed={len(closed)}"
                f" open={'1' if open_trade else '0'}"
            )
            last_print_year = today.year

    print(f"\nBacktest done in {time.time()-t0:.1f}s")
    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"]).set_index("date")
    return closed, eq_df


def report(trades: list[Trade], equity: pd.DataFrame, df: pd.DataFrame) -> None:
    print("\n" + "=" * 64)
    print(f"  RESULTS — Andrea on {SYMBOL} only (1:3 R:R, single ticker)")
    print("=" * 64)

    if not trades:
        print("No trades fired.")
        return

    # ---- Full trade log ----
    print("\nFULL TRADE LOG:")
    print(
        f"{'#':>3} {'entry_date':12} {'entry':>7} {'stop':>7} {'target':>7}"
        f" {'exit_date':12} {'exit':>7} {'reason':>7} {'R':>6} {'pnl$':>8}"
        f" {'days':>5} {'rsi':>5}"
    )
    print("-" * 110)
    for i, t in enumerate(trades, 1):
        held = (t.exit_date - t.entry_date).days if t.exit_date else 0
        print(
            f"{i:>3} {t.entry_date.date()!s:12}"
            f" {t.entry_price:>7.2f} {t.stop_price:>7.2f} {t.target_price:>7.2f}"
            f" {t.exit_date.date()!s:12} {t.exit_price:>7.2f} {t.exit_reason:>7}"
            f" {t.r_multiple:>+6.2f} {t.pnl_dollars:>+8.2f} {held:>5d}"
            f" {t.entry_rsi:>5.1f}"
        )

    # ---- Stats ----
    rows = pd.DataFrame(
        [
            {
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "exit_reason": t.exit_reason,
                "r_multiple": t.r_multiple,
                "pnl": t.pnl_dollars,
                "held_days": (t.exit_date - t.entry_date).days if t.exit_date else 0,
                "entry_rsi": t.entry_rsi,
                "range_atr_ratio": t.range_atr_ratio,
            }
            for t in trades
        ]
    )

    n = len(rows)
    wins = rows[rows["pnl"] > 0]
    losses = rows[rows["pnl"] <= 0]
    win_rate = len(wins) / n
    avg_win_r = wins["r_multiple"].mean() if len(wins) else 0.0
    avg_loss_r = losses["r_multiple"].mean() if len(losses) else 0.0
    expectancy = win_rate * avg_win_r + (1 - win_rate) * avg_loss_r
    total_pnl = rows["pnl"].sum()

    print("\nSUMMARY STATS:")
    print(f"  Total trades:    {n}")
    print(f"  Wins:            {len(wins)} ({win_rate:.1%})")
    print(f"  Losses:          {len(losses)} ({1 - win_rate:.1%})")
    print(f"  Avg win:         {avg_win_r:+.2f}R")
    print(f"  Avg loss:        {avg_loss_r:+.2f}R")
    print(f"  Expectancy:      {expectancy:+.2f}R per trade")
    print(f"  Total PnL:       ${total_pnl:+,.2f}")
    print(f"  Avg hold:        {rows['held_days'].mean():.1f} days")
    print(f"  Avg entry RSI:   {rows['entry_rsi'].mean():.1f}")
    print(f"  Avg range/ATR:   {rows['range_atr_ratio'].mean():.2f}x")

    print("\n  Exit reasons:")
    print(rows["exit_reason"].value_counts().to_string())

    # ---- Equity curve / strategy stats ----
    final_eq = equity["equity"].iloc[-1]
    total_return = final_eq / INITIAL_CAPITAL - 1
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (final_eq / INITIAL_CAPITAL) ** (1 / years) - 1 if years > 0 else 0
    rolling_max = equity["equity"].cummax()
    dd = (equity["equity"] - rolling_max) / rolling_max
    max_dd = dd.min()
    daily_ret = equity["equity"].pct_change().dropna()
    sharpe = (
        daily_ret.mean() / daily_ret.std() * np.sqrt(252)
        if daily_ret.std() > 0
        else 0
    )

    print("\nSTRATEGY EQUITY:")
    print(f"  Initial capital: ${INITIAL_CAPITAL:,.2f}")
    print(f"  Final equity:    ${final_eq:,.2f}")
    print(f"  Total return:    {total_return:+.1%}")
    print(f"  CAGR:            {cagr:+.1%}  ({years:.1f} years)")
    print(f"  Max drawdown:    {max_dd:.1%}")
    print(f"  Sharpe (daily):  {sharpe:.2f}")

    # ---- Buy & hold AMD comparison over same window ----
    bh_window = df[
        (df.index >= equity.index[0]) & (df.index <= equity.index[-1])
    ]
    if len(bh_window) >= 2:
        first_close = float(bh_window["close"].iloc[0])
        last_close = float(bh_window["close"].iloc[-1])
        bh_return = last_close / first_close - 1
        bh_cagr = (last_close / first_close) ** (1 / years) - 1 if years > 0 else 0
        # B&H drawdown
        bh_eq = bh_window["close"]
        bh_rmax = bh_eq.cummax()
        bh_dd = ((bh_eq - bh_rmax) / bh_rmax).min()

        print(f"\n{SYMBOL} BUY & HOLD COMPARISON (same window):")
        print(f"  Start price:     ${first_close:.2f} on {bh_window.index[0].date()}")
        print(f"  End price:       ${last_close:.2f} on {bh_window.index[-1].date()}")
        print(f"  Total return:    {bh_return:+.1%}")
        print(f"  CAGR:            {bh_cagr:+.1%}")
        print(f"  Max drawdown:    {bh_dd:.1%}")
        print(f"  Strategy edge:   {(cagr - bh_cagr) * 100:+.1f} pp/yr")

    # ---- Sample trades ----
    print("\nSAMPLE OF FIRST 5 TRADES:")
    for t in trades[:5]:
        held = (t.exit_date - t.entry_date).days
        print(
            f"  {t.entry_date.date()} buy @${t.entry_price:.2f}"
            f" -> {t.exit_date.date()} {t.exit_reason} @${t.exit_price:.2f}"
            f" R={t.r_multiple:+.2f} pnl=${t.pnl_dollars:+.2f} held={held}d"
        )
    if len(trades) > 5:
        print("\nSAMPLE OF LAST 5 TRADES:")
        for t in trades[-5:]:
            held = (t.exit_date - t.entry_date).days
            print(
                f"  {t.entry_date.date()} buy @${t.entry_price:.2f}"
                f" -> {t.exit_date.date()} {t.exit_reason} @${t.exit_price:.2f}"
                f" R={t.r_multiple:+.2f} pnl=${t.pnl_dollars:+.2f} held={held}d"
            )

    print("\n" + "=" * 64)
    print("  GO / NO-GO")
    print("=" * 64)
    if expectancy > 0.30 and win_rate > 0.30:
        print(
            f"GO — expectancy {expectancy:+.2f}R, win rate {win_rate:.0%}"
            f" (>30% on 1:3)."
        )
    elif expectancy > 0.10:
        print(f"MAYBE — expectancy {expectancy:+.2f}R is positive but thin.")
    else:
        print(f"NO-GO — expectancy {expectancy:+.2f}R near zero.")


def main() -> None:
    df = load_amd()
    trades, equity = run_backtest(df)
    report(trades, equity, df)


if __name__ == "__main__":
    main()
