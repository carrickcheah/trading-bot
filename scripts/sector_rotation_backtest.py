"""Sector Rotation backtest — 3-month momentum on SPDR sector ETFs.

Strategy:
  - Universe: 9 SPDR sector ETFs (XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU)
  - Benchmark: SPY
  - Regime filter: only hold positions when SPY > 200d MA (else go to cash)
  - Each month-end (last business day):
      * If SPY <= 200d MA -> close all positions, stay in cash
      * Else: rank 9 sectors by 3-month return, hold top 3 (equal weight ~33% each)
      * Sell any held sector that drops out of top 3, rotate into new ones
  - No stops/targets — long-term rotational. Exit only on rebalance or regime flip.
  - Initial capital: $10,000

If the ETF parquet files are missing under BARS_DIR, fetch them via yfinance
(auto_adjust=True, start 2010-01-01) and write them with the standard 8-column
schema (date, open, high, low, close, volume, trade_count=NaN, wap=NaN).

Costs:
  - Slippage: 0.1% per side
  - Commission: $0.005/share
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

BARS_DIR = Path("/data/bars")
SPY_TICKER = "SPY"
SECTOR_TICKERS = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU"]

# Backtest parameters
BACKTEST_START = pd.Timestamp("2012-01-01")
BACKTEST_END = pd.Timestamp("2026-04-29")
INITIAL_CAPITAL = 10_000.0
TOP_N = 3
MOMENTUM_LOOKBACK_MONTHS = 3
SPY_MA_DAYS = 200
FETCH_START = "2010-01-01"  # earlier than BACKTEST_START to allow lookbacks

# Costs
SLIPPAGE_PER_SIDE = 0.001  # 0.1%
COMMISSION_PER_SHARE = 0.005


@dataclass
class Position:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    cost_basis: float  # total $ paid (incl slippage but excluding commission)


@dataclass
class Rebalance:
    date: pd.Timestamp
    regime_on: bool
    held_before: list[str]
    held_after: list[str]
    momentum_ranking: list[tuple[str, float]]
    equity_before: float
    equity_after: float
    spy_close: float
    spy_ma200: float


@dataclass
class TradeRecord:
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    shares: int
    pnl_dollars: float
    return_pct: float
    reason: str  # "rotation", "regime_off"


# --------------------------------------------------------------------------- #
# Data acquisition
# --------------------------------------------------------------------------- #
def yf_to_df(yf_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance output to our 8-column schema."""
    if yf_df is None or yf_df.empty:
        return pd.DataFrame()
    if isinstance(yf_df.columns, pd.MultiIndex):
        yf_df.columns = yf_df.columns.get_level_values(0)
    yf_df = yf_df.reset_index()
    return pd.DataFrame({
        "date": yf_df["Date"].dt.strftime("%Y-%m-%d"),
        "open": yf_df["Open"].astype(float),
        "high": yf_df["High"].astype(float),
        "low": yf_df["Low"].astype(float),
        "close": yf_df["Close"].astype(float),
        "volume": yf_df["Volume"].astype(float),
        "trade_count": pd.NA,
        "wap": pd.NA,
    })


def ensure_etfs_present(tickers: list[str]) -> None:
    """Make sure each ticker has a parquet file in BARS_DIR with sufficient history."""
    BARS_DIR.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    for t in tickers:
        f = BARS_DIR / f"{t}.parquet"
        if not f.exists():
            missing.append(t)
            continue
        try:
            df = pd.read_parquet(f, columns=["date"])
            if df.empty:
                missing.append(t)
                continue
            first = pd.to_datetime(df["date"].astype(str).min())
            last = pd.to_datetime(df["date"].astype(str).max())
            # Need at least ~2010 to 2026 for our window with lookback
            if first > pd.Timestamp("2011-01-01") or last < BACKTEST_END:
                missing.append(t)
        except Exception:
            missing.append(t)
    if not missing:
        print(f"All {len(tickers)} tickers present.")
        return

    print(f"Fetching {len(missing)} missing tickers via yfinance: {missing}")
    import yfinance as yf
    for t in missing:
        try:
            yf_df = yf.download(
                t,
                start=FETCH_START,
                end=(BACKTEST_END + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            df = yf_to_df(yf_df)
            if df.empty or len(df) < 500:
                print(f"  {t}: no/insufficient data ({len(df)} rows) — skipping")
                continue
            out = BARS_DIR / f"{t}.parquet"
            df.to_parquet(out, compression="snappy")
            print(f"  {t}: wrote {len(df)} rows ({df['date'].iloc[0]} → {df['date'].iloc[-1]})")
        except Exception as e:
            print(f"  {t}: ERROR {e}")


# --------------------------------------------------------------------------- #
# Bars loading
# --------------------------------------------------------------------------- #
def load_bars(tickers: list[str]) -> dict[str, pd.DataFrame]:
    print(f"Loading bars from {BARS_DIR}...")
    bars: dict[str, pd.DataFrame] = {}
    t0 = time.time()
    for t in tickers:
        f = BARS_DIR / f"{t}.parquet"
        if not f.exists():
            print(f"  WARNING: {t}.parquet missing")
            continue
        df = pd.read_parquet(f, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"].astype(str))
        df = df.dropna(subset=["close"]).drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
        df.set_index("date", inplace=True)
        bars[t] = df
    print(f"Loaded {len(bars)} tickers in {time.time()-t0:.1f}s")
    return bars


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def get_month_ends(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Last business day of each month between start and end."""
    return list(pd.date_range(start, end, freq="BME"))


def price_at(df: pd.DataFrame, date: pd.Timestamp, col: str = "close") -> float | None:
    """Latest price <= date (forward-fill semantics)."""
    if df.empty:
        return None
    if date in df.index:
        return float(df.loc[date, col])
    pos = df.index.get_indexer([date], method="ffill")[0]
    if pos < 0:
        return None
    return float(df.iloc[pos][col])


def next_open_after(df: pd.DataFrame, date: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    """Return (date, open) for the first bar strictly after `date`."""
    pos = df.index.get_indexer([date], method="bfill")[0]
    # bfill returns the same date if present; we want strictly after
    if pos < 0:
        return None
    if df.index[pos] == date and pos + 1 < len(df.index):
        pos += 1
    if pos >= len(df.index):
        return None
    return df.index[pos], float(df.iloc[pos]["open"])


def compute_3mo_momentum(df: pd.DataFrame, rank_date: pd.Timestamp) -> float | None:
    """Return ratio price(rank_date) / price(rank_date - 3 months) - 1."""
    p_now = price_at(df, rank_date)
    if p_now is None or p_now <= 0:
        return None
    lookback = rank_date - pd.DateOffset(months=MOMENTUM_LOOKBACK_MONTHS)
    p_then = price_at(df, lookback)
    if p_then is None or p_then <= 0:
        return None
    return p_now / p_then - 1.0


def mark_to_market(cash: float, positions: dict[str, Position], bars: dict[str, pd.DataFrame], today: pd.Timestamp) -> float:
    total = cash
    for sym, pos in positions.items():
        px = price_at(bars[sym], today)
        if px is None:
            px = pos.entry_price
        total += pos.shares * px
    return total


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #
def run_backtest(bars: dict[str, pd.DataFrame], spy: pd.DataFrame) -> tuple[
    list[Rebalance], list[TradeRecord], pd.DataFrame
]:
    print(f"\nBacktest Sector Rotation: {BACKTEST_START.date()} → {BACKTEST_END.date()}")
    print(f"  Top {TOP_N} of {len(SECTOR_TICKERS)} sectors, {MOMENTUM_LOOKBACK_MONTHS}-month momentum")
    print(f"  Regime filter: SPY > {SPY_MA_DAYS}d MA, monthly rebalance, no stops/targets")
    print(f"  Initial capital: ${INITIAL_CAPITAL:,.0f}\n")

    cash = INITIAL_CAPITAL
    positions: dict[str, Position] = {}
    closed_trades: list[TradeRecord] = []
    rebalances: list[Rebalance] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []

    # Pre-compute SPY 200d MA series
    spy_with_ma = spy.copy()
    spy_with_ma["ma200"] = spy_with_ma["close"].rolling(SPY_MA_DAYS, min_periods=SPY_MA_DAYS).mean()

    month_ends = get_month_ends(BACKTEST_START, BACKTEST_END)
    next_rebal_idx = 0
    pending_action: dict | None = None  # action queued from rebalance, executed at next open

    trading_days = pd.bdate_range(BACKTEST_START, BACKTEST_END)
    t0 = time.time()

    def execute_rotation(today: pd.Timestamp, target_set: list[str], reason: str) -> tuple[list[str], list[str]]:
        """Sell holdings not in target_set, then buy any target not currently held.

        Sells executed at today's open * (1 - slippage). Buys at today's open * (1 + slippage).
        Equal weight: each NEW position = (cash_after_sells) / (TOP_N - already_held_in_target).
        Returns (sold, bought).
        """
        nonlocal cash
        sold: list[str] = []
        bought: list[str] = []

        # 1. Sell positions not in target_set
        for sym in list(positions.keys()):
            if sym in target_set:
                continue
            pos = positions[sym]
            if today not in bars[sym].index:
                # No bar today — fall back to last close
                px = price_at(bars[sym], today)
                if px is None:
                    continue
                exit_open = px
            else:
                exit_open = float(bars[sym].loc[today, "open"])
            exit_price = exit_open * (1 - SLIPPAGE_PER_SIDE)
            proceeds = pos.shares * exit_price - pos.shares * COMMISSION_PER_SHARE
            cash += proceeds
            pnl = proceeds - pos.cost_basis - pos.shares * COMMISSION_PER_SHARE  # already netted commish on entry
            # Simpler: pnl = proceeds_minus_entry_cost
            pnl = (exit_price - pos.entry_price) * pos.shares - 2 * pos.shares * COMMISSION_PER_SHARE
            ret = (exit_price / pos.entry_price - 1.0) if pos.entry_price > 0 else 0.0
            closed_trades.append(TradeRecord(
                symbol=sym,
                entry_date=pos.entry_date,
                exit_date=today,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                shares=pos.shares,
                pnl_dollars=pnl,
                return_pct=ret,
                reason=reason,
            ))
            del positions[sym]
            sold.append(sym)

        # 2. Buy targets not yet held
        to_buy = [s for s in target_set if s not in positions]
        if not to_buy:
            return sold, bought
        per_slot = cash / len(to_buy) * 0.99  # tiny buffer for commission
        for sym in to_buy:
            if today not in bars[sym].index:
                continue
            entry_open = float(bars[sym].loc[today, "open"])
            entry_price = entry_open * (1 + SLIPPAGE_PER_SIDE)
            shares = int(per_slot / entry_price)
            if shares <= 0:
                continue
            cost = shares * entry_price + shares * COMMISSION_PER_SHARE
            if cost > cash + 1e-6:
                # Reduce shares by 1 if rounding tipped us over
                shares -= 1
                if shares <= 0:
                    continue
                cost = shares * entry_price + shares * COMMISSION_PER_SHARE
                if cost > cash + 1e-6:
                    continue
            cash -= cost
            positions[sym] = Position(
                symbol=sym,
                entry_date=today,
                entry_price=entry_price,
                shares=shares,
                cost_basis=shares * entry_price,
            )
            bought.append(sym)

        return sold, bought

    for day_idx, today in enumerate(trading_days):
        # --- 1. Execute pending rebalance action at today's OPEN ---
        if pending_action is not None and today >= pending_action["execute_on_or_after"]:
            held_before = sorted(positions.keys())
            target = pending_action["target"]
            reason = pending_action["reason"]
            equity_before = mark_to_market(cash, positions, bars, today)
            sold, bought = execute_rotation(today, target, reason)
            held_after = sorted(positions.keys())
            equity_after = mark_to_market(cash, positions, bars, today)
            spy_close = price_at(spy_with_ma, today) or float("nan")
            spy_ma_val = float(spy_with_ma["ma200"].asof(today)) if not spy_with_ma["ma200"].asof(today) is None else float("nan")
            try:
                spy_ma_val = float(spy_with_ma["ma200"].asof(today))
            except Exception:
                spy_ma_val = float("nan")

            rebalances.append(Rebalance(
                date=today,
                regime_on=pending_action["regime_on"],
                held_before=held_before,
                held_after=held_after,
                momentum_ranking=pending_action["ranking"],
                equity_before=equity_before,
                equity_after=equity_after,
                spy_close=spy_close,
                spy_ma200=spy_ma_val,
            ))
            pending_action = None

        # --- 2. Monthly rebalance check (signal at end-of-day, execute next open) ---
        if next_rebal_idx < len(month_ends) and today >= month_ends[next_rebal_idx]:
            rank_date = today
            spy_close = price_at(spy_with_ma, rank_date) or float("nan")
            spy_ma_now = spy_with_ma["ma200"].asof(rank_date)
            spy_ma_now = float(spy_ma_now) if pd.notna(spy_ma_now) else float("nan")
            regime_on = bool(pd.notna(spy_ma_now) and spy_close > spy_ma_now)

            ranking: list[tuple[str, float]] = []
            for sym in SECTOR_TICKERS:
                if sym not in bars:
                    continue
                m = compute_3mo_momentum(bars[sym], rank_date)
                if m is None:
                    continue
                ranking.append((sym, m))
            ranking.sort(key=lambda x: x[1], reverse=True)

            if not regime_on:
                target_set: list[str] = []  # all-cash
                reason = "regime_off"
            else:
                target_set = [s for s, _ in ranking[:TOP_N]]
                reason = "rotation"

            pending_action = {
                "execute_on_or_after": rank_date + pd.Timedelta(days=1),
                "target": target_set,
                "ranking": ranking,
                "regime_on": regime_on,
                "reason": reason,
            }
            next_rebal_idx += 1

        equity_curve.append((today, mark_to_market(cash, positions, bars, today)))

    # If we finish with a pending action, just end (no execution)
    print(f"Backtest done in {time.time()-t0:.1f}s")
    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"]).set_index("date")
    return rebalances, closed_trades, eq_df


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report(
    rebalances: list[Rebalance],
    trades: list[TradeRecord],
    equity: pd.DataFrame,
    spy: pd.DataFrame,
) -> None:
    print("\n" + "=" * 64)
    print("  RESULTS — Sector Rotation (3-month momentum, top 3 of 9)")
    print("=" * 64)

    # Strategy stats
    final_eq = float(equity["equity"].iloc[-1])
    total_return = final_eq / INITIAL_CAPITAL - 1
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (final_eq / INITIAL_CAPITAL) ** (1 / years) - 1 if years > 0 else 0.0
    rolling_max = equity["equity"].cummax()
    dd = (equity["equity"] - rolling_max) / rolling_max
    max_dd = float(dd.min())
    daily_ret = equity["equity"].pct_change().dropna()
    sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0.0

    print(f"\nFinal equity:      ${final_eq:,.0f}")
    print(f"Total return:      {total_return:+.1%}")
    print(f"CAGR:              {cagr:+.2%}")
    print(f"Max drawdown:      {max_dd:.1%}")
    print(f"Sharpe (daily):    {sharpe:.2f}")
    print(f"Years tracked:     {years:.2f}")

    # SPY benchmark
    spy_aligned = spy["close"].reindex(equity.index, method="ffill")
    spy_return = float("nan")
    spy_cagr = float("nan")
    spy_dd = float("nan")
    spy_sharpe = float("nan")
    if not spy_aligned.empty and pd.notna(spy_aligned.iloc[0]) and spy_aligned.iloc[0] > 0:
        spy_return = float(spy_aligned.iloc[-1] / spy_aligned.iloc[0] - 1)
        spy_cagr = float((spy_aligned.iloc[-1] / spy_aligned.iloc[0]) ** (1 / years) - 1)
        spy_eq = (spy_aligned / spy_aligned.iloc[0]) * INITIAL_CAPITAL
        spy_rmax = spy_eq.cummax()
        spy_dd = float(((spy_eq - spy_rmax) / spy_rmax).min())
        spy_daily = spy_aligned.pct_change().dropna()
        spy_sharpe = float(spy_daily.mean() / spy_daily.std() * np.sqrt(252)) if spy_daily.std() > 0 else 0.0

    print(f"\nSPY buy-and-hold benchmark:")
    print(f"  Total return:    {spy_return:+.1%}")
    print(f"  CAGR:            {spy_cagr:+.2%}")
    print(f"  Max drawdown:    {spy_dd:.1%}")
    print(f"  Sharpe:          {spy_sharpe:.2f}")
    print(f"  vs strategy:     {(cagr - spy_cagr)*100:+.2f} pp/yr CAGR")

    # Rebalance / monthly hit-rate analysis
    print(f"\nRebalance count:   {len(rebalances)}")
    if rebalances:
        regime_off = sum(1 for r in rebalances if not r.regime_on)
        regime_on = len(rebalances) - regime_off
        print(f"  Regime ON:       {regime_on}")
        print(f"  Regime OFF:      {regime_off}")

    # Hit rate vs SPY: month-on-month
    eq_monthly = equity["equity"].resample("ME").last().pct_change().dropna()
    spy_monthly = spy_aligned.resample("ME").last().pct_change().dropna()
    common_idx = eq_monthly.index.intersection(spy_monthly.index)
    if len(common_idx) > 0:
        eq_m = eq_monthly.loc[common_idx]
        spy_m = spy_monthly.loc[common_idx]
        beat = (eq_m > spy_m).sum()
        total = len(common_idx)
        hit = beat / total
        print(f"\nMonthly hit rate vs SPY: {beat}/{total} = {hit:.1%}")

    # Trades
    print(f"\nClosed trades:     {len(trades)}")
    if trades:
        wins = sum(1 for t in trades if t.pnl_dollars > 0)
        win_rate = wins / len(trades)
        avg_ret = sum(t.return_pct for t in trades) / len(trades)
        avg_hold = sum((t.exit_date - t.entry_date).days for t in trades) / len(trades)
        total_pnl = sum(t.pnl_dollars for t in trades)
        print(f"  Win rate:        {win_rate:.1%}")
        print(f"  Avg return/trd:  {avg_ret:+.2%}")
        print(f"  Avg hold days:   {avg_hold:.0f}")
        print(f"  Total trade PnL: ${total_pnl:+,.0f}")
        # Exit reasons
        from collections import Counter
        reasons = Counter(t.reason for t in trades)
        print(f"  Exit reasons:    {dict(reasons)}")

    # Sector frequency
    if rebalances:
        from collections import Counter
        held_count: Counter = Counter()
        for r in rebalances:
            for s in r.held_after:
                held_count[s] += 1
        print(f"\nSector hold frequency (months held):")
        for s, c in held_count.most_common():
            print(f"  {s}: {c}")

    # Last 5 rebalances summary
    print(f"\nLast 5 rebalances:")
    for r in rebalances[-5:]:
        regime = "ON " if r.regime_on else "OFF"
        rank_str = ", ".join(f"{s}{m*100:+.1f}%" for s, m in r.momentum_ranking[:5])
        held_str = ",".join(r.held_after) if r.held_after else "CASH"
        print(f"  {r.date.date()} regime={regime} held={held_str:20s} top: {rank_str}")

    print("\n" + "=" * 64)
    print("  GO / NO-GO")
    print("=" * 64)
    edge = cagr - (spy_cagr if pd.notna(spy_cagr) else 0)
    if cagr > 0 and edge > 0.005 and max_dd > -0.40:
        print(f"GO — strategy CAGR {cagr:+.2%} beats SPY by {edge*100:+.2f} pp/yr with manageable DD.")
    elif cagr > 0 and edge > -0.01:
        print(f"MAYBE — strategy CAGR {cagr:+.2%} ~ SPY's {spy_cagr:+.2%}. Marginal benefit (DD/Sharpe trade-off?).")
    else:
        print(f"NO-GO — strategy CAGR {cagr:+.2%} vs SPY {spy_cagr:+.2%}. No edge here.")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    all_tickers = SECTOR_TICKERS + [SPY_TICKER]
    ensure_etfs_present(all_tickers)
    bars = load_bars(all_tickers)
    if SPY_TICKER not in bars:
        raise SystemExit(f"SPY missing from {BARS_DIR}; cannot run regime filter / benchmark.")
    missing = [t for t in SECTOR_TICKERS if t not in bars]
    if missing:
        raise SystemExit(f"Missing sector ETFs: {missing}")
    spy = bars[SPY_TICKER]
    rebalances, trades, equity = run_backtest(bars, spy)
    report(rebalances, trades, equity, spy)


if __name__ == "__main__":
    main()
