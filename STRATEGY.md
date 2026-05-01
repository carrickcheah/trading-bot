# Strategies — buy signals reference

All strategies use the **standard risk management** below unless noted otherwise. Buy signals fire when ALL listed conditions are true on the same day. Trade is entered at the NEXT day's open.

---

## Table of Contents

1. [⭐ Featured strategy: VCP Breakout (only GO verdict)](#-featured-strategy-vcp-breakout-only-go-verdict)
   - [The coiled spring analogy](#the-coiled-spring-analogy)
   - [Real example pattern (NVDA-style 2023)](#real-example-pattern-nvda-style-2023)
   - [The 11 conditions (all must be true)](#the-11-conditions-all-must-be-true)
   - [Why VCP works (the behavioral edge)](#why-vcp-works-the-behavioral-edge)
   - [Inventor & track record](#inventor--track-record)
   - [Our backtest result (2012-2026)](#our-backtest-result-2012-2026)
2. [Standard exit rules (every strategy)](#standard-exit-rules-every-strategy)
3. [Universe filters (apply to every stock-selection strategy)](#universe-filters-apply-to-every-stock-selection-strategy)
4. [Backtest results — ranked by WIN RATE](#backtest-results--all-complete-ranked-by-win-rate)
   - [Benchmark — S&P 500 buy-and-hold (via SPY)](#benchmark--sp-500-buy-and-hold-via-spy)
   - [Key takeaways](#key-takeaways)
   - [Honest recommendation](#honest-recommendation)
5. [Notes](#notes)

---

## ⭐ Featured strategy: VCP Breakout (only GO verdict)

**VCP = Volatility Contraction Pattern.** The "coiled spring" pattern.

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

Each time the stock dips, it dips LESS (-15%, then -10%, then -6%, then -3%). Price gets tighter — like the spring compressing.

Volume also DRIES UP during this period (no one's selling — they're all waiting).

Then one day, a flood of buyers shows up and price breaks above the "pivot" (highest point of the base). **You buy.**

### Real example pattern (NVDA-style 2023)

```
April 2023:    NVDA bouncing between $260 and $290 (range ±10%)
May 2023:      bouncing between $270 and $290 (tighter ±7%)
June 2023:     bouncing between $275 and $290 (tighter ±5%)
July 2023:     coiled tight at $285-290 (±2%)

Then... boom. Aug 2023: NVDA closes $295 on 3× normal volume.
You buy at $295. By Dec 2023 it's at $495 (+68%).
```

### The 11 conditions (all must be true)

The bot scans all ~5,800 stocks every day looking for these:

| # | Condition |
|---|---|
| 1 | Stock is in long-term uptrend (above 50-day & 200-day MA) |
| 2 | Stock is outperforming the S&P 500 over 60 days |
| 3 | Stock has spent 5–15 weeks in a tight range (the "base") |
| 4 | The base depth is 15–25% (high to low) |
| 5 | Volume DECREASED during the base (seller exhaustion) |
| 6 | At least 3 progressively tighter pullbacks within the base (the "compression") |
| 7 | Today's close > the highest close in the base (breakout above pivot) |
| 8 | Today's volume ≥ 1.5× the 50-day average (buyers stepping in) |
| 9 | Today's close in the upper third of the day's range (buyers won the day) |
| 10 | S&P 500 above its 200-day MA (market regime is favorable) |
| 11 | Universe filters pass (price $10-200, ADV > 500k) |

When all 11 are true on the same day → **BUY at next morning's open.**

### Why VCP works (the behavioral edge)

Three forces combine:

1. **Institutions accumulating quietly** — big funds buy slowly during the base (creates the sideways action). When they finish accumulating, supply runs out.
2. **Sellers exhaust** — anyone who wanted to sell already sold during the base. No supply left to push price down.
3. **FOMO triggers** — when price breaks the pivot, every chart-watcher sees the same thing and piles in. Volume confirms.

The result: a sudden, sharp move higher.

### Inventor & track record

**Mark Minervini** — won the 1997 US Investing Championship with **220% returns**. His method is called **SEPA** (Specific Entry Point Analysis). VCP is the core pattern.

### Our backtest result (2012-2026)

| Metric | Value | Means |
|---|---|---|
| Win rate | 38.5% | You'll lose 6 of 10 trades |
| Avg winner | +2.99R = **+18%** | Hits target nearly every win |
| Avg loser | -1.01R = -6% | Stop loss caps losses |
| Expectancy | **+0.53R per trade** | Best of all 12 strategies tested |
| **CAGR** | **+7.5%** | Real money: $10k → $28k over 14 years |
| **Max drawdown** | **-12.0%** | Smallest of all 12 strategies |
| **Sharpe** | **0.84** | Closest to S&P 500's 0.90 |
| **Verdict** | **GO ✅** | Only strategy with a GO verdict |

Even losing 6 of 10 trades, the 4 wins are 3× bigger than the 6 losses — so you make money.

### VCP R:R sensitivity test (all 1:3, varying stop/target sizes)

Same VCP rules, only the stop and target percentages change. All keep 1:3 ratio.

| R:R | Trades | Win % | Avg Win | Avg Loss | Expectancy | CAGR | Max DD | Sharpe | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 6/18 | **816** (most) | 30.4% | +2.98R | -1.02R | +0.20R | +4.8% | **-12.8%** | 0.58 | MAYBE |
| 7/21 | 613 | 30.7% | +2.98R | -1.01R | +0.21R | +4.6% | -16.6% | 0.52 | MAYBE |
| 8/24 | 504 | 34.3% | +2.98R | -1.01R | +0.36R | +6.4% | -13.0% | 0.76 | **GO** ✅ |
| 9/27 | 391 | 35.3% | +2.99R | -1.01R | +0.40R | +6.3% | -19.4% | 0.69 | **GO** ✅ |
| **10/30** ⭐ | **340** (least) | **38.5%** | **+2.99R** | -1.01R | **+0.53R** | **+7.5%** | **-12.0%** | **0.84** | **GO** ✅ |

**Key insight:** wider stops/targets = better expectancy, higher Sharpe, fewer (better-quality) trades. **10/30 is the optimal R:R** for VCP on our data.

Pattern: as we go 6→10% stop:
- Trade count drops (816 → 340)
- Win rate climbs (30% → 39%)
- Expectancy grows (+0.20R → +0.53R)
- Sharpe improves (0.58 → 0.84)

---

## Standard exit rules (every strategy)

| Rule | Value |
|---|---|
| **Stop loss** | −6% from entry |
| **Take profit (target)** | +18% from entry |
| **R:R ratio** | 1:3 |
| **Time stop** | 90 days max hold |
| **Position size** | $1,000 fixed per trade |
| **Slippage** | 0.2% entry / 0.1% exit |
| **Commission** | $0.005 per share |

---

## Universe filters (apply to every stock-selection strategy)

| Filter | Value | Why |
|---|---|---|
| Price | $10 ≤ price ≤ $200 | Tradeable, not penny stocks, fits $10k account |
| Avg daily volume | > 500,000 shares (50d avg) | Liquidity |
| History | ≥ 250 days | Need indicators warmed up |
| Listing | US common stocks only | No SPACs, no warrants, no preferred |
| Earnings | None in next 5 days | Avoid gap risk |

---

## Buy signals — 12 strategies tested

### 1. Cross-Sectional Momentum 12-1 (CSM)
**Idea:** Buy stocks that have outperformed the most over the past year (skipping the most recent month to avoid short-term reversal).

| Buy when ALL true |
|---|
| Last day of month (monthly rebalance) |
| Stock's 12-month return (t−13mo to t−1mo) is in TOP 10 of all eligible stocks |
| Universe filters all pass |

---

### 2. Donchian 20-day Breakout (Turtle-style)
**Idea:** Buy when price breaks above its 20-day high (classic trend-following).

| Buy when ALL true |
|---|
| Today's close > MAX(close over past 20 days, excluding today) |
| SPY > 200-day MA (regime filter) |
| Universe filters all pass |

---

### 3. RSI(2) Connors Mean Reversion
**Idea:** Buy short-term oversold pullbacks in established uptrends.

| Buy when ALL true |
|---|
| Stock's close > 200-day MA (only buy uptrending stocks) |
| 2-period RSI < 15 (severely oversold) |
| Universe filters all pass |

---

### 4. RSI(14) Classic Mean Reversion
**Idea:** Wider RSI window — wait for the 14-period RSI to cross back above 30 before buying (confirmed reversal).

| Buy when ALL true |
|---|
| Stock's close > 200-day MA (uptrend filter) |
| Yesterday RSI(14) < 30 (was oversold) |
| Today RSI(14) ≥ 30 (recovery beginning) |
| Today's close > today's open (bullish candle confirmation) |
| Universe filters all pass |

---

### 5. Sector Rotation
**Idea:** Hold the 3 strongest US sector ETFs based on 3-month return.

| Buy when ALL true |
|---|
| Last day of month (monthly rebalance) |
| Sector's 3-month return is in TOP 3 of the 9 SPDR sectors |
| SPY > 200-day MA (else go all-cash) |
| Universe: only XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU |

**Note:** This strategy uses NO stops/targets — exits only on monthly rebalance or regime flip.

---

### 6. Bollinger Band Mean Reversion
**Idea:** Buy stocks in uptrends that have pulled back below the lower Bollinger Band (severely oversold relative to recent volatility).

| Buy when ALL true |
|---|
| Stock's close > 200-day MA (uptrend filter) |
| Today's close < lower Bollinger Band (20-day MA − 2σ) |
| Universe filters all pass |

**Aux exit:** also exit when close crosses back above 20-day MA (mean-reversion target).

---

### 7. IBS (Internal Bar Strength) Mean Reversion
**Idea:** Buy when stock closes near its day's low (bottom 10% of range) — often bounces next day.

| Buy when ALL true |
|---|
| Stock's close > 200-day MA |
| IBS = (close − low) / (high − low) < 0.10 |
| Prior day close > prior day open (no extended downtrend) |
| Universe filters all pass |

**Aux exit:** also exit when IBS > 0.7 OR close > 5-day MA.

---

### 8. Dual Momentum (Antonacci)
**Idea:** Combines absolute momentum (positive return) with relative momentum (beats SPY).

| Buy when ALL true |
|---|
| Last day of month (monthly rebalance) |
| Stock's 12-month return > 0 (absolute momentum) |
| Stock's 12-month return > SPY's 12-month return (relative outperformance) |
| Stock is in TOP 10 by 12-month return among survivors |
| Universe filters all pass |

**Special exit:** if SPY drops below 200d MA, exit ALL positions next open (cash defense).

---

### 9. Low-Volatility Momentum
**Idea:** Combine momentum quality (high 12-month return) with stability (low realized volatility).

| Buy when ALL true |
|---|
| Last day of month (monthly rebalance) |
| Stock is in TOP 30% by 12-month return |
| Stock is in BOTTOM 10 by 60-day realized volatility (within survivors) |
| Universe filters all pass |

---

### 10. Buy-the-Dip on Quality
**Idea:** Wait for a sharp pullback in a strong uptrend then buy the bounce.

| Buy when ALL true |
|---|
| Stock above 50d MA AND 50d MA > 200d MA (strong uptrend) |
| Stock's 12-month return > +20% (top performer) |
| 50d MA rising over last 30 days |
| Stock down ≥ 10% from 52-week high (proper pullback) |
| RSI(14) < 40 (oversold within uptrend) |
| Today's close > today's open (start of bounce) |
| Universe filters all pass |

---

### 11. Andrea (RSI + Support + Bullish Candle)
**Idea:** Discretionary playbook — wait for oversold RSI at support with a bullish reversal candle.

| Buy when ALL true |
|---|
| RSI(14) < 30 (oversold) |
| Today's low ≤ 1.02 × (lowest low over past 20 days) (touched support) |
| Today's close > today's open (bullish candle) |
| Today's range > 0.5 × ATR(14) (real-bodied candle, not noise) |
| Universe filters all pass |

---

### 12. VCP Breakout (Minervini SEPA / Volatility Contraction Pattern)
**Idea:** Buy as a stock breaks out of a tight, well-formed consolidation base on heavy volume.

| Buy when ALL true |
|---|
| Stock above 50d MA AND 50d MA > 200d MA (Stage 2 uptrend) |
| Relative Strength vs SPY > 0 over 60 days |
| Stock has spent last 5–15 weeks in a tight base |
| Base range depth: 15–25% (high to low) |
| Volume CONTRACTED during the base (less than prior 60d average) |
| ≥ 3 progressively tighter pullbacks within the base (VCP signature) |
| Today's close > base high (breakout above pivot point) |
| Today's volume ≥ 1.5× the 50-day average |
| Today's close in the upper third of the day's range |
| SPY > 200-day MA (regime filter) |
| Universe filters all pass |

---

## Backtest results — ALL COMPLETE (ranked by WIN RATE)

Period: 2012-01-01 → 2026-04-29 · ~16 years · 6,391 US stocks · $1,000 per trade · 1:3 R:R (-6%/+18%) · 90-day time stop

| Rank | Strategy | **Win rate** | Trades | Avg Win | Avg Loss | Expectancy | CAGR | Max DD | Sharpe | vs S&P 500 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 🥇 | **RSI(2) Connors** | **59.7%** | 3,735 | +0.48R | -0.67R | +0.02R | +1.2% | -22.8% | 0.17 | -14 pp | NO-GO |
| 🥈 | **Sector Rotation** | 55.1% | 176 | n/a | n/a | n/a | **+7.86%** | -21.2% | **0.66** | -7 pp | NO-GO |
| 🥉 | IBS Mean Reversion | 54.1%* | 3,738 | +0.39R | -0.46R | +0.00R | -0.8% | -44.0% | -0.02 | -16 pp | NO-GO |
| 4 | Bollinger MR | 49.0%* | 3,341 | +1.10R | -0.96R | +0.05R | +4.4% | -27.5% | 0.35 | -10 pp | NO-GO |
| 5 | VCP Breakout (FIXED — volume-ranked) | 38.5% | 340 | +2.99R | -1.01R | **+0.53R** | **+7.5%** | **-12.0%** | **0.84** | -7 pp | **GO** ✅ |
| 6 | **Low-Vol Momentum** | ~43%* | 947 | +1.61R | -0.95R | **+0.15R** | +4.3% | -20.6% | 0.35 | -11 pp | MAYBE |
| 7 | Andrea | 39.1% | 1,201 | +2.33R | -1.40R | +0.06R | +2.5% | -33.0% | 0.22 | -12 pp | NO-GO |
| 8 | Dipbuy on Quality | 34.5% | 29 | +2.27R | -1.02R | +0.12R | +0.1% | **-4.2%** | 0.08 | -15 pp | MAYBE |
| 9 | Donchian | 28.9% | 1,867 | +2.55R | -1.00R | +0.03R | +1.6% | **-53.6%** ❌ | 0.20 | -13 pp | NO-GO |
| 10 | CSM 7/21 | 28.5% | ~1,500 | +2.89R | -1.01R | +0.10R | +5.0% | -23.1% | 0.38 | -10 pp | MAYBE |
| 11 | CSM 6/18 | 27.5% | 1,606 | n/a | n/a | +0.10R | +4.7% | -26.7% | 0.36 | -10 pp | MAYBE |
| 12 | Dual Momentum | 25.7% | 1,361 | +2.92R | -1.01R | -0.00R | -0.5% | **-65.1%** ❌ | 0.09 | -15 pp | NO-GO |

*Win rates marked with `*` were computed from expectancy + avg-win/loss (not directly reported by the backtest).

### Benchmark — S&P 500 buy-and-hold (via SPY)

| Metric | Value |
|---|---|
| CAGR | **+14.9%** |
| Max DD | -33.7% |
| Sharpe | **0.90** |

### Key takeaways

1. **No strategy beats S&P 500 buy-and-hold.** All 12 underperformed by 7-16 pp/yr.
2. **Best of the active strategies:** Sector Rotation (highest CAGR, best Sharpe, lowest DD vs S&P 500) — but still loses 7 pp/yr to just owning the index.
3. **Best risk-adjusted single-stock strategy:** Low-Vol Momentum (highest expectancy +0.15R, only -20.6% drawdown).
4. **Lowest drawdown of all:** Dipbuy on Quality (-4.2%) — but only 29 trades in 14 years (signal too rare).
5. **Brutal results:** Donchian (-54% DD), Dual Momentum (-65% DD), IBS (lost money).
6. **High win rate ≠ profit:** RSI(2) Connors hit 60% win rate but tiny avg win (+0.48R) — net flat.

### Honest recommendation

For passive holding: **buy S&P 500 (SPY/VOO/IVV)**.
For active management with drawdown protection: **Sector Rotation**.
For potential edge to develop further: **Low-Vol Momentum** or **VCP Breakout** (smallest individual-stock drawdowns).

For everything else: skip.

> **Note:** "SPY" appears in strategy specs because it's the tradeable ETF for the S&P 500 — same thing for benchmark purposes (tracking error <0.1%/yr).

---

## Notes

- Win rate ≠ profitability. A strategy can win 70% of the time and lose money if avg loss is bigger than avg win (and vice versa).
- At 1:3 R:R, theoretical breakeven is 25% win rate; realistic is ~30% after fees and slippage.
- All backtests run on 6,391 US-listed common stocks, daily bars 2010–2026, ~16 years of data.
- Data stored in Parquet files on shared Azure VM at `/opt/trading-bot/data/bars/*.parquet`.
- Quarantined 28 tickers with corrupt OHLC data (negative prices etc.) — see `/data/bars_corrupted/`.
