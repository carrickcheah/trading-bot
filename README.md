# Swing Breakout Bot + OpenClaw Assistant — Full Plan

**Strategy:** Long-only swing breakout (consolidation base → volume breakout)
**Market:** US Equities, daily bars, holding weeks to months
**R:R:** 1:3 (5% stop, 15% target)
**Stack:** Python 3.12+ execution engine + OpenClaw read-only AI assistant
**Broker:** Interactive Brokers (IBKR)
**Market Data:** IBKR (live + recent historical) · Yahoo Finance (long-horizon daily, RS-vs-SPY filter)
**Deployment:** Azure VM (East US 2 region)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│  After-close: OpenClaw → news, catalysts → Telegram     │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  Python Swing Bot (execution layer)                     │
│  - Daily scanner → Universe → Entry/Manage → Exit       │
│  - Writes to: trades.db, bot.log, state.json            │
│  - Talks to: IBKR (orders + market data, single conn)   │
└─────────────────────────────────────────────────────────┘
                          ↓ (read-only)
┌─────────────────────────────────────────────────────────┐
│  OpenClaw (assistant layer, isolated user)              │
│  - Reads bot DB/logs, sends alerts, answers questions   │
│  - EOD reports, weekly reviews, pattern detection       │
│  - NO order authority. NO broker credentials.           │
└─────────────────────────────────────────────────────────┘
                          ↓
                       Telegram
                          ↓
                         You
```

---

## Strategy Specification

### Universe (daily scan, after market close)

- Price between **$10 and $200**
- Average daily volume > **500,000 shares**
- US-listed common stock (no SPACs, no OTC, no ADRs without ADV check)
- No earnings in the **next 7 days**
- Float > 5M shares (avoid micro-float manipulation)
- Top 30–50 ranked candidates per scan

### Trend Filters (must ALL pass)

- Price > **50-day moving average**
- 50-day MA > **200-day MA** (golden cross alignment)
- Stock not down >20% in the last 30 days (no falling knives)
- Relative Strength vs SPY > 0 over the last 60 days

### Setup Detection (consolidation base)

- Stock has spent the last **4–8 weeks in a tight range**
- Range width: high to low **within 10–15%**
- Average volume during the base is **lower** than the prior 60-day average (volume contraction)
- The pattern looks "boring" on the chart — that is the point

### Buy Trigger

- Daily close **above the base high** (the highest close of the last 4–8 weeks)
- Today's volume ≥ **2× the 50-day average volume**
- Close in the **upper third** of today's daily range (not a fakeout)
- Order placed: **Market on Open (MOO) for next session**, or limit at +0.5% above breakout level

### Stop & Target

- **Stop: 5% below entry** (or below the breakout level / base low — whichever is tighter, but never wider than 5%)
- **Target: 15% above entry** (1:3 R:R)
- Optional: scale 50% off at +10% (2R), trail the rest with the 20-day moving average

### Position Sizing

- Risk **0.5–1% of account** per trade
- Shares = (account × risk%) / (entry − stop)
- Hard cap: **15% of account per position**
- Hard cap: **25% of account in any single sector**

### Time & Portfolio Rules

- Maximum **5 open positions** at any time
- Maximum **2 new entries per week**
- **3-month time stop:** if a position has not hit +1R or stop after 12 weeks, exit at next market open
- **Pause new entries** if SPY closes below its 200-day MA (bear-regime filter)

### Expected Performance

- Win rate: **38–45%**
- Average win: **+1.8R to +2.5R** (some hit full +3R, some trail out earlier)
- Average loss: **−1R** (hard-capped by stop)
- Frequency: **80–150 trades/year**
- Expected annual return: **20–40%** in normal regimes
- Expected drawdown: **15–25%**

---

## Math: Why 38%+ Win Rate Profits at 1:3

- Theoretical breakeven: **25%** win rate
- Real breakeven (after fees, slippage): **~30%**
- 38% × 100 trades × +2R avg − 62% × 100 × −1R = **+76R − 62R = +14R**
- 45% × 100 trades at full 1:3 = **+135R − 55R = +80R**
- **Profit = expectancy × frequency**, not win rate alone
- Asymmetric outcomes: losses are capped at −1R; rare runners can deliver +5R or more when trailed correctly

---

## Phase 0 — Foundation (Week 1)

### Accounts & subscriptions
- Interactive Brokers (5–10 days approval) — cash account is fine, no PDT concerns
- IBKR US Securities Snapshot & Futures Value Bundle (~$10/mo, often waived if commissions ≥ $30/mo)
- IBKR news subscription (Reuters / Briefing.com — free or low-cost tiers via TWS)
- Yahoo Finance (free, via `yfinance`) — long-horizon daily bars and SPY benchmark
- Azure subscription — VM (Standard B2s, Ubuntu 22.04 LTS, East US 2)
- Telegram bot via BotFather

### Toolchain
- Python 3.12+, `uv` for dependency / venv management, Git
- **Core libs:** `ib_async` (IBKR TWS wrapper, async), `pandas` + `pyarrow` (data + Parquet), `yfinance` (Yahoo fallback), `pydantic-settings` (typed `.env` config), `structlog` (JSON-line logs), `python-telegram-bot` (alerts), `sqlite3` (stdlib, no ORM)
- **Dev tooling:** `pytest`, `pytest-asyncio`, `ruff` (lint + format), `mypy --strict` (type checking — non-negotiable)
- systemd unit hardening (OpenClaw + bot isolation via dedicated Linux users — same VM, no Docker)
- Azure NSG (network security group) for egress allowlisting

**Why Python, not C++:** swing trading decides on daily bars, after the close. Latency is irrelevant. Python's iteration speed and library ecosystem (pandas, yfinance, ML overlays) make it the obvious choice.

**Why long-only:** retail traders compound better with one direction mastered than with two directions half-mastered. Shorts have asymmetric risk (unlimited upside loss), borrow costs, and require more reactive management. Long-only is the right beginner discipline.

---

## Phase 1 — Data Layer (Week 2)

- `MarketDataFeed` Protocol (`typing.Protocol`) — single contract for live and historical
- `LiveFeed` — IBKR streaming via `ib_async` for daily bar updates and intraday confirmation
- `HistoricalFeed` — IBKR `ib.reqHistoricalData()` for daily bars going back 5+ years; `yfinance` for long-horizon daily and SPY benchmark; outputs cached to local Parquet
- **Pacing-aware request queue** — IBKR enforces ~60 historical requests / 10 min and 50 concurrent open requests. Use `asyncio.Semaphore(50)` plus a token-bucket rate limiter
- Symbol metadata refresh nightly (float, ADV, sector) via IBKR contract details + Yahoo fallback
- 5+ years of daily bars, splits/dividends adjusted, **includes delisted symbols** (supplement with Yahoo or a one-time vendor dump for delisted names)
- Pre-computed indicators stored in cache: 20/50/200-day MA, 60-day relative strength vs SPY, 50-day average volume, ATR(14)

**Deliverable:** Replay 5 years of daily bars for 100+ representative tickers (small/mid-cap names from the trading universe), verify bar accuracy and indicator computation against a second source.

---

## Phase 2 — Backtest Engine (Week 3)

Event-driven, single-threaded, deterministic.

- `EventLoop` — consumes daily bars chronologically (sync iterator for backtest; live mode subscribes to end-of-day bar updates)
- `Strategy` Protocol — `on_bar(bar)`, `on_fill(fill)`, `on_timer(now)` (snake_case per PEP 8)
- `Portfolio` — open positions, cash, P&L (immutable updates via `@dataclass(frozen=True)`)
- `OrderManager` — order lifecycle state machine, supports MOO (Market on Open), LMT, STP
- `ExecutionSimulator` — slippage (0.1–0.3% on entry, 0.05% on exit at planned levels), commissions ($0.005/share, $1 minimum), partial fills

**Critical:** the same `Strategy` class runs backtest AND live — only the feed and order router differ. Dependency injection at construction time, no global state, no `if backtest: ... else: ...` branches inside strategy logic.

**Deliverable:** Buy-and-hold dummy strategy produces correct P&L; `mypy --strict` passes; the same class is exercised in both backtest and a live-paper smoke test.

---

## Phase 3 — Daily Scanner (Week 4)

The scanner runs **every trading day at 4:30 PM ET** (after market close) on the day's official close prices. It produces tomorrow's candidate buy list.

### Scanner pipeline

1. **Universe pass** — start with all US-listed common stocks, ~5,000 tickers
2. **Liquidity filter** — ADV > 500K, price $10–$200, float > 5M
3. **Earnings filter** — drop tickers with earnings in next 7 days
4. **Trend filter** — keep only tickers above their 50-day MA, with 50-day above 200-day, RS vs SPY > 0
5. **Base detection** — keep tickers in a tight 4–8 week consolidation (range ≤15%, contracted volume)
6. **Trigger check** — flag tickers whose latest close broke above the base high on ≥2× average volume, closing in the upper third of the day
7. **Rank** — sort by composite score (volume confirmation × RS strength × tightness of base)
8. **Output** — top 5–10 ranked candidates written to `signals` table for tomorrow's open

**Deliverable:** Scanner output for past dates matches manual chart inspection on at least 20 sample days across multiple market regimes (bull, bear, sideways).

---

## Phase 4 — Swing Strategy Implementation (Week 5)

Implement the full strategy spec above on the live event loop.

### Trade Database Schema (`trades.db`)

```sql
CREATE TABLE trades (
  trade_id TEXT PRIMARY KEY,
  symbol TEXT,
  side TEXT,                  -- 'LONG' (always, this is long-only)
  entry_date DATE,
  entry_price REAL,
  stop_price REAL,
  target_price REAL,
  exit_date DATE,
  exit_price REAL,
  exit_reason TEXT,           -- target, stop, trail, time_stop, regime_pause
  shares INTEGER,
  risk_dollars REAL,
  r_multiple REAL,
  pnl_dollars REAL,
  base_low REAL,
  base_high REAL,
  base_weeks INTEGER,         -- length of consolidation base
  breakout_volume_ratio REAL, -- breakout vol / 50d avg vol
  rs_vs_spy_60d REAL,
  sector TEXT,
  catalyst TEXT
);

CREATE TABLE signals (
  scan_date DATE,
  symbol TEXT,
  signal_type TEXT,           -- 'BUY_BREAKOUT'
  passed BOOLEAN,
  filters_failed TEXT,
  composite_score REAL
);

CREATE TABLE bot_state (
  ts DATETIME,
  status TEXT,
  open_positions INTEGER,
  total_exposure_pct REAL,
  daily_pnl_dollars REAL,
  weekly_pnl_dollars REAL,
  errors_count INTEGER
);

CREATE TABLE positions (
  symbol TEXT PRIMARY KEY,
  entry_date DATE,
  entry_price REAL,
  shares INTEGER,
  current_stop REAL,          -- updated as trail moves
  target_price REAL,
  unrealized_r REAL,
  days_held INTEGER
);
```

`bot.log`: JSON lines, one event per line, parseable by OpenClaw.

**Deliverable:** Strategy runs against 5 years of historical data, produces a trade log that matches manual inspection on 20+ sample trades.

---

## Phase 5 — Backtest Validation (Week 6)

- 60% in-sample tuning / 20% out-of-sample test (one shot) / 20% reserved
- **Metrics:** win rate, expectancy, Sharpe (>1.0), profit factor (>1.5), max DD (<25%), CAGR (>15%)
- **Stress tests:** remove top 10 trades, 2× slippage, yearly breakdowns, regime breakdowns (bull/bear/sideways)
- **Parameter sensitivity:** ±20% on every knob (base length, volume threshold, stop %, target %)
- **Monte Carlo trade-order shuffle** (worst 5% equity curves)
- **Cross-validation by sector:** strategy must work across multiple sectors, not just tech

**Go/no-go:** OOS within 30% of in-sample. If yes, proceed. If no, strategy is overfit.

### The Seven Backtesting Sins (avoid all)

1. Look-ahead bias (using close to decide on close)
2. Survivorship bias (training only on currently-listed names)
3. Slippage too low (use 0.2% minimum on entry)
4. Liquidity assumption (size > 1% of bar volume)
5. Overfitting (too many parameters, too few trades)
6. Ignoring fees (commissions + spread + financing)
7. Cherry-picked time period (must include 2008, 2020, 2022 drawdowns)

---

## Phase 6 — Live Infrastructure (Week 7)

- IBKR TWS API via `ib_async` (the bot connects to a local IB Gateway or TWS instance)
- Live data feed adapter conforming to `MarketDataFeed` Protocol — daily bar updates at the close
- Live order router conforming to `OrderManager` — supports MOO, LMT, STP orders for next-day execution
- **Reconciliation:** bot-vs-broker positions/orders/cash every 30 minutes during market hours, and at end of day
- **Heartbeat:** bot writes timestamp every 60s
- **Kill switch:** file-watcher (`/var/run/orb-bot/KILL`) flattens all positions and halts new entries
- **Pre-trade checks:** every order validated against position cap (15% per name), sector cap (25%), and total exposure limit

**Deliverable:** Bot connects to IBKR paper account, places MOO orders for tomorrow's open, reconciles cleanly the next day after fills.

---

## Phase 7 — OpenClaw Assistant Setup (Week 8)

Run OpenClaw on the **same Azure VM** as the Python bot, but under a dedicated unprivileged Linux user with systemd hardening. **No broker credentials. Read-only on bot artifacts.** No Docker — isolation comes from Linux uid + file permissions + systemd sandboxing + Azure NSG egress rules.

### Filesystem layout

```text
/opt/orb-bot/                 owner: orb-bot:orb-bot   mode: 750
  ├── src/orb_bot/                                      mode: 750   # Python package
  ├── pyproject.toml          owner: orb-bot:orb-bot   mode: 644   # uv-managed deps
  ├── uv.lock                 owner: orb-bot:orb-bot   mode: 644
  ├── .venv/                  owner: orb-bot:orb-bot   mode: 750   # uv-created venv
  ├── secrets/.env            owner: orb-bot:orb-bot   mode: 600   # IBKR creds — openclaw CANNOT read
  ├── trades.db               owner: orb-bot:openclaw  mode: 640   # bot writes, openclaw reads
  ├── bot.log                 owner: orb-bot:openclaw  mode: 640
  ├── state.json              owner: orb-bot:openclaw  mode: 640
  └── news_cache.db           owner: orb-bot:openclaw  mode: 640

/opt/openclaw/                owner: openclaw:openclaw mode: 750
  ├── state/                                            mode: 750
  └── secrets/.env            owner: openclaw:openclaw mode: 600   # Telegram + Anthropic keys ONLY
```

The `openclaw` user has no shell, no sudo, and no group membership that grants access to `/opt/orb-bot/secrets/`. File-permission isolation is the primary security boundary.

### systemd unit: `/etc/systemd/system/openclaw.service`

```ini
[Unit]
Description=OpenClaw Assistant (read-only)
After=network-online.target orb-bot.service
Wants=network-online.target

[Service]
User=openclaw
Group=openclaw
WorkingDirectory=/opt/openclaw
EnvironmentFile=/opt/openclaw/secrets/.env
ExecStart=/usr/local/bin/openclaw run --config /opt/openclaw/config.yaml
Restart=on-failure
RestartSec=10s

# Sandboxing
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
NoNewPrivileges=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6
RestrictNamespaces=true
LockPersonality=true
MemoryDenyWriteExecute=true

# Filesystem access
ReadOnlyPaths=/opt/orb-bot/trades.db /opt/orb-bot/bot.log /opt/orb-bot/state.json /opt/orb-bot/news_cache.db
ReadWritePaths=/opt/openclaw/state
InaccessiblePaths=/opt/orb-bot/secrets

[Install]
WantedBy=multi-user.target
```

### Network egress (Azure NSG, applied to the VM)

OpenClaw's traffic is allowlisted to two destinations only:
- `api.telegram.org` (HTTPS 443)
- `api.anthropic.com` (HTTPS 443)

Everything else — including arbitrary outbound from a compromised skill — is dropped at the NSG. The bot's IBKR traffic uses a separate outbound rule keyed to the broker gateway IP range.

### Skills to install/build

1. `bot_reader` — reads `trades.db`, `positions`, `bot.log`, and the cached news table
2. `news_brief` — formats per-ticker news headlines (sourced upstream by the Python bot via IBKR news API)
3. `telegram_messenger` — formatted alerts (Telegram is the **only** notification channel — no email, no SMS)
4. `scheduler` — Heartbeat Engine for time-based triggers
5. `report_generator` — daily/weekly summaries

### Custom Skill: `bot_reader/SKILL.md`

```markdown
# Bot Reader Skill

Reads the Python swing bot's trade database and structured logs.

## Tools
- get_open_positions() → current positions, days held, unrealized R-multiple
- get_recent_trades(n) → last N closed trades with R outcomes
- get_today_signals() → tomorrow's buy candidates from the scanner
- get_bot_status() → last heartbeat, errors, exposure %, SPY regime
- query_trades(start, end, filters) → historical trade query

## Constraints
- READ ONLY. Never write. Never call broker APIs.
- Never recommend acting on data without a human in the loop.
- If bot status shows errors, alert immediately via Telegram.
```

---

## Phase 8 — Scheduled Workflows (Week 9)

### 17:00 ET Daily — End-of-Day Brief

OpenClaw:
1. Read today's scanner output (tomorrow's candidates)
2. Read last-24h news per candidate from cached news table
3. Tag catalysts: earnings beat / guidance / FDA / analyst / sector momentum
4. Read open positions and their R-multiples
5. Send brief to Telegram

```
🌙 EOD Brief — Apr 30
SPY regime: ✅ above 200d MA — entries enabled
Open positions: 3 (NVDA +1.4R, PLTR +0.6R, SOFI -0.3R)
Total exposure: 35% of account

Tomorrow's buy candidates (top 5):
🔹 RIVN  — 6w base, +120% volume, RS strong
🔹 COIN  — 5w base, +180% volume, crypto momentum
🔹 DKNG  — 7w base, +95% volume, sector strength
🔹 ANET  — 4w base, +110% volume, AI infrastructure
🔹 MARA  — 8w base, +200% volume, BTC catalyst

Bot status: ready, last heartbeat 16:59
```

### Real-time — Fill Alerts

```
✅ FILLED: LONG NVDA
Entry: 845.30 | Stop: 802.85 (-5%) | Target: 972.10 (+15%)
Risk: $720 (0.8%) | Shares: 17
Base: 6w consolidation $820–$840 | Vol: 2.4× | RS: +12% vs SPY
Catalyst: earnings beat, guidance raise
```

### Real-time — Anomaly Alerts

Triggers:
- Position approaching stop (within 1% of stop price)
- Position approaching target (within 2% of target)
- SPY breaks below 200d MA (regime flip — pause new entries)
- Total exposure >70% of account
- Bot heartbeat gap >5 min during market hours
- Reconciliation mismatch (bot vs broker)

### Friday 17:00 — Weekly Review

- Trades opened / closed this week
- Win rate (rolling 20 trades)
- Average R per win, average R per loss
- Live vs backtest expectancy comparison
- Pattern detection (best/worst sectors, base lengths, volume ratios)
- Drawdown / streak status
- Recommendations: continue / tweak / pause

### On-Demand via Telegram

- "Status?" → bot health + open positions + total R
- "Why no entries today?" → reads signals table, lists why each candidate failed
- "Show last 10 trades" → table with R-multiples
- "Pause bot" → OpenClaw replies: *"I can't do that — touch the kill file or use IBKR."*

---

## Phase 9 — Paper Trading (Week 10)

Both systems running. Python bot trades paper, OpenClaw assists.

### Validation criteria
- Paper performance within 30% of backtest expectation
- All OpenClaw alerts firing correctly, no false positives
- No bot crashes or unexplained errors
- 20+ trades observed and trusted (note: at swing frequency, this means 4–8 weeks of paper trading minimum)

---

## Phase 10 — Live, Small Size (Week 11)

- Start at 1/4 intended size (0.25% risk per trade vs target 1%)
- 30+ live trades minimum before scaling (note: at swing frequency, this is 3–6 months of live trading)
- Compare live R-distribution to backtest
- Scale: 2× → 4× → full size, 20+ trades between each step

---

## Phase 11 — Operations (Week 12+)

- **Daily:** glance at OpenClaw EOD brief
- **Weekly:** review pattern report → continue / tweak / pause
- **Monthly:** parameter drift, regime check, sector rotation review
- **Quarterly:** full re-backtest on recent data

### Pause Triggers (OpenClaw alerts)
- Rolling 20-trade expectancy < 0
- 8+ losing trades in a row
- Live slippage 2×+ backtest for >1 month
- Win rate drops below 30% rolling
- SPY below 200d MA for >2 weeks

---

## Hard Rules — Never Break

1. OpenClaw never gets broker credentials. Ever.
2. OpenClaw never writes to bot's DB or config.
3. Kill switch is a file or web endpoint, not a chat command.
4. Every code path live = code path that ran in backtest.
5. Trading bot has zero LLM dependencies in hot path. The Python decision loop is `ib_async` + pure Python — no API calls to Anthropic, OpenClaw, or any external model.
6. Run OpenClaw under a dedicated unprivileged Linux user with systemd hardening; egress restricted to Telegram + Anthropic via Azure NSG. IBKR credentials live in a directory the `openclaw` user has no permission to read.
7. Audit OpenClaw outputs weekly — LLMs hallucinate.
8. **Long only.** No shorts, no margin abuse, no options. Mastery > optionality.
9. **Never override the stop.** A stop hit is information, not a problem.

---

## Timeline (Aggressive — 1 Week Per Phase)

| Phase | Duration | Cumulative |
|---|---|---|
| 0 — Foundation | 1 wk | 1 wk |
| 1 — Data layer | 1 wk | 2 wk |
| 2 — Backtest engine | 1 wk | 3 wk |
| 3 — Scanner | 1 wk | 4 wk |
| 4 — Strategy implementation | 1 wk | 5 wk |
| 5 — Validation | 1 wk | 6 wk |
| 6 — Live infra | 1 wk | 7 wk |
| 7 — OpenClaw setup | 1 wk | 8 wk |
| 8 — Workflows | 1 wk | 9 wk |
| 9 — Paper trading | 1 wk | 10 wk |
| 10 — Live small | 1 wk | 11 wk |
| 11 — Operations | ongoing | wk 12+ |

**~11 weeks to live capital.** This is an aggressive plan, not a realistic one.

### Where 1 week breaks down

- **Phase 9 (Paper Trading):** the validation criteria call for "20+ trades observed." At swing frequency (~2 entries/week), 1 calendar week gives only 1–2 trades. Honest minimum: **4–8 weeks** of paper trading.
- **Phase 10 (Live, Small Size):** "30+ live trades" cannot fit in 1 week. Honest minimum: **3–6 months** of live small-size trading.
- **Phase 2 (Backtest Engine) and Phase 6 (Live Infrastructure):** historically the two phases that slip most. Event loop correctness and broker reconciliation are where bugs hide.

Treat this timeline as the **aspirational floor**. If a phase blows past 1 week, the right move is to extend, not to skip the deliverable.

---

## This Week's Action Items

1. Open IBKR account; enable US Securities Snapshot & Futures Value Bundle market data and a news subscription
2. Provision Azure VM (Standard B2s, Ubuntu 22.04 LTS, East US 2); harden SSH, create `orb-bot` and `openclaw` system users with the `/opt/...` filesystem layout from Phase 7, install Python 3.12+ and `uv`, configure NSG egress allowlist
3. Create Telegram bot via BotFather, save token to Azure Key Vault (or `.env` for dev)
4. Skim `ib_async` README and IBKR TWS API pacing rules — focus on `reqHistoricalData` semantics and the ~60 requests / 10 min ceiling
5. Bootstrap the project: `uv init`, add deps (`ib_async`, `pandas`, `pyarrow`, `yfinance`, `pydantic-settings`, `structlog`, `python-telegram-bot`, `pytest`, `ruff`, `mypy`), then start Phase 1 by pulling 5 years of daily bars for 100 representative tickers and computing the 20/50/200-day MAs

---

## Common Failure Points

- Skipping paper trading because backtest looks good → loses on day 1
- Building everything custom → use libraries
- Optimizing for speed before correctness → get it right first
- Trading live before reconciliation works → bot/broker disagree, chaos
- No kill switch → first bad day wipes you out
- Not logging everything → impossible to debug
- Letting OpenClaw scope-creep into execution path → ruins discipline, adds hallucination risk
- **Buying breakouts that already extended +20%** — you missed it; wait for the next base
- **Ignoring the 200d MA regime filter** — long-only strategies bleed in bear markets without it
- **Chasing volume** — a breakout on 1.2× volume is a fake; wait for 2×+
- **Adding a "few extra rules" to fit recent losses** — that's overfitting; backtest the rule on past data first

---

## The One Rule That Matters Most

**Never let the live bot do something the backtest didn't simulate.**
Every live code path must have run in backtest first. This catches 90% of disasters.

---
