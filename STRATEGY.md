# VCP Breakout — strategy reference

This is the **only strategy with a GO verdict** after testing 12 systematic approaches on 16 years of US equity data.

---

## Table of Contents

1. [What VCP is — beginner explanation](#what-vcp-is--beginner-explanation)
   - [The coiled spring analogy](#the-coiled-spring-analogy)
   - [Real example pattern](#real-example-pattern)
2. [The 11 buy conditions](#the-11-buy-conditions)
3. [Why VCP works (behavioral edge)](#why-vcp-works-behavioral-edge)
4. [Final spec for live trading](#final-spec-for-live-trading)
5. [Backtest result](#backtest-result)
6. [R:R sensitivity (5 variants tested)](#rr-sensitivity-5-variants-tested)
7. [Time-stop sensitivity (5 variants tested)](#time-stop-sensitivity-5-variants-tested)
8. [Universe filters](#universe-filters)
9. [Benchmark — S&P 500](#benchmark--sp-500)
10. [Notes](#notes)

---

## What VCP is — beginner explanation

**VCP = Volatility Contraction Pattern.** Mark Minervini's "coiled spring" pattern.

### The coiled spring analogy

Imagine a spring being slowly compressed. Each push gets a little smaller because the spring is getting tighter. Eventually it CAN'T compress more, so when you let go — **BANG** — it explodes upward.

A stock can do the same thing:

```
Price                                    BREAKOUT! 🚀
                                            ↑
                                         ──┘
$50 ──── pivot point ─────────────────────
                                       ↗
                                     ↗
                                   ↗
                       ← getting tighter →
                       ↘   ↗   ↘  ↗  ↘ ↗
                        ↘ ↗     ↘↗   ↘↗
                         ↘
$45 ── base low ─────────────────────────

         ←── 6-12 weeks of "boring" ──→  ↑
                                         today
```

Each time the stock dips, it dips LESS (-15%, then -10%, then -6%, then -3%). Price gets tighter — like a spring compressing.

Volume also DRIES UP during this period (no one's selling — everyone's waiting).

Then one day, a flood of buyers shows up and price breaks above the "pivot" (highest point of the base). **You buy.**

### Real example pattern

```
April 2023:    NVDA bouncing between $260 and $290 (range ±10%)
May 2023:      bouncing between $270 and $290 (tighter ±7%)
June 2023:     bouncing between $275 and $290 (tighter ±5%)
July 2023:     coiled tight at $285-290 (±2%)

Then... boom. Aug 2023: NVDA closes $295 on 3× normal volume.
You buy at $295. By Dec 2023 it's at $495 (+68%).
```

---

## The 11 buy conditions

The bot scans ~5,800 US stocks every day looking for these. **All 11 must be true on the same day** to fire a buy signal.

| # | Condition |
|---|---|
| 1 | Stock above 50-day MA AND 200-day MA (long-term uptrend) |
| 2 | Outperforming S&P 500 over past 60 days |
| 3 | Stock has spent 5–15 weeks in a tight range (the "base") |
| 4 | Base depth: 15–25% (high to low) |
| 5 | Volume DECREASED during the base (seller exhaustion) |
| 6 | At least 3 progressively tighter pullbacks within the base (compression) |
| 7 | Today's close > base high (breakout above pivot) |
| 8 | Today's volume ≥ 1.5× the 50-day average |
| 9 | Today's close in upper third of the day's range |
| 10 | S&P 500 above its 200-day MA (regime favorable) |
| 11 | Universe filters pass (price $10-200, ADV > 500k) |

When all 11 fire → **BUY at next morning's open.**

### Selection: rank by signal strength

If many stocks qualify on the same day, the bot picks the **top 5 by volume ratio** (strongest breakouts win the slots).

---

## Why VCP works (behavioral edge)

Three forces combine:

1. **Institutions accumulating quietly** — big funds buy slowly during the base (creates sideways action). When they finish, supply runs out.
2. **Sellers exhaust** — anyone who wanted to sell already sold during the base. No supply left to push price down.
3. **FOMO triggers** — when price breaks the pivot, every chart-watcher sees the same thing and piles in. Volume confirms.

Result: a sudden, sharp move higher.

### Inventor

**Mark Minervini** — won the 1997 US Investing Championship with **220% returns**. His method is called **SEPA** (Specific Entry Point Analysis). VCP is the core pattern.

---

## Final spec for live trading

**Optimal parameters from sensitivity testing:**

| Parameter | Value |
|---|---|
| **Stop loss** | −10% from entry |
| **Take profit (target)** | +30% from entry |
| **R:R ratio** | 1:3 |
| **Time stop** | 730 days (2 years) max hold |
| **Position size** | $1,000 fixed per trade |
| **Max positions** | 5 simultaneous |
| **Slippage** | 0.2% entry / 0.1% exit |
| **Commission** | $0.005 per share |

---

## Backtest result

**Period: 2012-01-01 → 2026-04-29 · 14.3 years · 6,391 US stocks · $10,000 starting capital**

| Metric | Value |
|---|---|
| Total trades | 343 |
| **Win rate** | **38.8%** |
| Avg winner | +2.95R = +29% |
| Avg loser | -1.01R = -10% |
| **Expectancy** | **+0.53R per trade** |
| Avg hold | 131 days (~4.4 months) |
| Final equity | $28,224 |
| **CAGR** | **+7.5%** |
| **Max drawdown** | **-12.1%** (smallest of all 12 strategies tested) |
| **Sharpe** | **0.84** |
| **Verdict** | **GO ✅** |

Even losing 6 of 10 trades, the 4 wins are 3× bigger than the 6 losses → math wins.

---

## R:R sensitivity (5 variants tested)

Same VCP rules, varying stop/target % (all keep 1:3 ratio):

| R:R | Trades | Win % | Expectancy | CAGR | Max DD | Sharpe | Verdict |
|---|---|---|---|---|---|---|---|
| 6/18 | 816 | 30.4% | +0.20R | +4.8% | -12.8% | 0.58 | MAYBE |
| 7/21 | 613 | 30.7% | +0.21R | +4.6% | -16.6% | 0.52 | MAYBE |
| 8/24 | 504 | 34.3% | +0.36R | +6.4% | -13.0% | 0.76 | GO |
| 9/27 | 391 | 35.3% | +0.40R | +6.3% | -19.4% | 0.69 | GO |
| **10/30** ⭐ | **343** | **38.8%** | **+0.53R** | **+7.5%** | **-12.1%** | **0.84** | **GO** |

**Insight:** Wider stops/targets = better expectancy + Sharpe. **10/30 is optimal.**

Pattern (6→10% stop):
- Trade count drops (816 → 343)
- Win rate climbs (30% → 39%)
- Expectancy grows (+0.20R → +0.53R)
- Sharpe improves (0.58 → 0.84)

---

## Time-stop sensitivity (5 variants tested)

Same VCP 10/30 rules, varying max hold time:

| Time stop | Trades | Win % | Avg Win | Expectancy | CAGR | Max DD | Sharpe | Verdict |
|---|---|---|---|---|---|---|---|---|
| 90 days | 703 | **47.9%** | +1.36R | +0.19R | +6.1% | -20.0% | 0.66 | GO |
| 120 days | 615 | 43.7% | +1.65R | +0.19R | +5.6% | -19.0% | 0.56 | GO |
| 180 days | 475 | 43.6% | +2.05R | +0.35R | +7.0% | -19.8% | 0.73 | GO |
| 365 days | 390 | 37.9% | +2.68R | +0.40R | +6.8% | -18.2% | 0.75 | GO |
| **730 days (2yr)** ⭐ | **343** | **38.8%** | **+2.95R** | **+0.53R** | **+7.5%** | **-12.1%** | **0.84** | **GO** |
| ∞ (no time stop) | 340 | 38.5% | +2.99R | +0.53R | +7.5% | -12.0% | 0.84 | GO |

**Key finding:** **730 days = ∞** in performance. Gives same +7.5% CAGR but caps capital lock-up at 2 years.

**Pattern:** Shorter time stop cuts winners early (smaller avg win, lower expectancy). 2 years is the practical sweet spot.

---

## Universe filters

| Filter | Value | Why |
|---|---|---|
| Price | $10 ≤ price ≤ $200 | Tradeable, not penny stocks, fits $10k account |
| Avg daily volume | > 500,000 shares (50d avg) | Liquidity |
| History | ≥ 250 days | Need indicators warmed up |
| Listing | US common stocks only | No SPACs, no warrants, no preferred |
| Earnings | None in next 5 days | Avoid gap risk |

---

## Benchmark — S&P 500

| Metric | S&P 500 (via SPY) | VCP 10/30 |
|---|---|---|
| CAGR | **+14.9%** | +7.5% |
| Max DD | -33.7% | **-12.1%** |
| Sharpe | **0.90** | 0.84 |

**S&P 500 wins on raw return.** VCP wins on drawdown protection (-12% vs -34%).

If you want max return → buy S&P 500.
If you want active management with smaller drawdowns → run VCP.

To match S&P 500's CAGR with VCP, you'd need to combine multiple strategies or use 1.5-2× position sizing.

---

## Notes

- All backtests on 6,391 US-listed common stocks, daily bars 2010–2026.
- Data stored as Parquet on shared Azure VM at `/opt/trading-bot/data/bars/*.parquet`.
- 28 tickers quarantined for corrupt OHLC data (negative prices etc.) — in `/data/bars_corrupted/`.
- Win rate ≠ profitability. At 1:3 R:R, breakeven is ~25% win rate; realistic ~30% after fees.
- 11 other strategies tested (CSM, Donchian, RSI, Bollinger, IBS, Andrea, Sector Rotation, Dual Momentum, Low-Vol Momentum, Dipbuy, RSI-14) — all NO-GO or MAYBE. Details in git history (commit `153e284` and earlier).
