# Strategies — buy signals reference

All strategies use the **standard risk management** below unless noted otherwise. Buy signals fire when ALL listed conditions are true on the same day. Trade is entered at the NEXT day's open.

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

## Backtest results (so far)

| # | Strategy | Win rate | Expectancy | CAGR | Max DD | Sharpe | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | CSM 6/18 | 27.5% | +0.10R | +4.7% | −26.7% | 0.36 | MAYBE |
| 1b | CSM 7/21 | 28.5% | +0.10R | +5.0% | −23.1% | 0.38 | MAYBE |
| 2 | Donchian | 28.9% | +0.03R | +1.6% | −53.6% | 0.20 | NO-GO |
| 3 | RSI(2) Connors | 59.7% | +0.02R | +1.2% | −22.8% | 0.17 | NO-GO |
| 4 | RSI(14) classic | (running) | — | — | — | — | — |
| 5 | Sector Rotation | 55.1% | n/a | +7.86% | −21.2% | 0.66 | NO-GO (vs SPY +14.9%) |
| 6 | Bollinger MR | (running) | — | — | — | — | — |
| 7 | IBS MR | (running) | — | — | — | — | — |
| 8 | Dual Momentum | (running) | — | — | — | — | — |
| 9 | Low-Vol Momentum | (running) | — | — | — | — | — |
| 10 | Dipbuy on Quality | (running) | — | — | — | — | — |
| 11 | Andrea | (running, now with 1:3 ratio) | — | — | — | — | — |
| 12 | VCP Breakout (MVP) | 45.7% | +0.12R | +2.2% | −14.0% | 0.38 | MAYBE |

**Benchmark — SPY buy-and-hold (2012–2026):** CAGR +14.9%, Max DD −33.7%, Sharpe 0.90.

---

## Notes

- Win rate ≠ profitability. A strategy can win 70% of the time and lose money if avg loss is bigger than avg win (and vice versa).
- At 1:3 R:R, theoretical breakeven is 25% win rate; realistic is ~30% after fees and slippage.
- All backtests run on 6,391 US-listed common stocks, daily bars 2010–2026, ~16 years of data.
- Data stored in Parquet files on shared Azure VM at `/opt/trading-bot/data/bars/*.parquet`.
- Quarantined 28 tickers with corrupt OHLC data (negative prices etc.) — see `/data/bars_corrupted/`.
