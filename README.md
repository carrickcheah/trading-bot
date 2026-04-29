# ORB Trading Bot + OpenClaw Assistant — Full Plan

**Strategy:** Opening Range Breakout (ORB) with Volume + Relative Strength filter
**Market:** US Equities (intraday, no overnight holds)
**R:R:** 1:3
**Stack:** Python 3.12+ execution engine + OpenClaw read-only AI assistant
**Broker:** Interactive Brokers (IBKR)
**Market Data:** IBKR (live + recent historical) · Yahoo Finance (long-horizon daily, RS-vs-SPY filter)
**Deployment:** Azure VM (East US 2 region)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Pre-market: OpenClaw → news, catalysts → Telegram brief│
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  Python ORB Bot (execution layer)                       │
│  - Scanner → Universe → OR tracking → Entry/Exit        │
│  - Writes to: trades.db, bot.log, state.json            │
│  - Talks to: IBKR (orders + market data, single conn)   │
└─────────────────────────────────────────────────────────┘
                          ↓ (read-only)
┌─────────────────────────────────────────────────────────┐
│  OpenClaw (assistant layer, isolated container)         │
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

### Universe (pre-market scan, 9:00–9:25 ET)

- Price $5–$100
- Average daily volume > 1M
- Pre-market volume > 500K
- Gap > 2% from prior close
- Float < 100M
- Top 10–20 ranked candidates per day

### Opening Range

- Window: 9:30–9:45 ET (15 minutes)
- Track: Range High (RH), Range Low (RL), Range Size (RS = RH − RL)

### Filters (ALL must pass)

- Relative volume during OR > 2× same-window 20-day average
- Stock %move during OR > SPY %move (longs); inverse (shorts)
- RS between 0.5× and 2× daily ATR(14)
- Price above VWAP (longs) / below VWAP (shorts) at breakout
- No earnings in next 24 hours

### Entry

- Long: break of RH + 1 tick (or 0.05% buffer)
- Short: break of RL − 1 tick
- Confirmation: breakout candle closes beyond level on > average OR-bar volume
- Order type: marketable limit

### Stop & Target

- Stop: opposite side of OR. Risk = RS.
- Target: 3R
- Optional: scale 50% at 2R, trail rest with 1-min EMA20

### Position Sizing

- Risk 0.5–1% of account per trade
- Shares = (account × risk%) / (entry − stop)
- Hard cap: 10% of account per position

### Time & Daily Rules

- No new entries after 11:00 ET
- Exit if not at 1R by 11:30 ET
- Force flat by 15:45 ET
- Max 5 trades/day
- Halt at −2R daily loss
- Skip FOMC/CPI mornings until 10:30

### Expected Performance

- Win rate: 33–38%
- Expectancy: +0.4 to +0.7 R/trade
- Frequency: 150–300 trades/year
- Drawdown: 15–25% expected

---

## Math: Why 30%+ Win Rate Profits at 1:3

- Theoretical breakeven: 25% win rate
- Real breakeven (after fees, slippage): ~30–32%
- 35% win rate × 100 trades = +40R net
- 40% win rate × 100 trades = +80R net
- **Profit = expectancy × frequency**, not win rate alone

---

## Phase 0 — Foundation (Week 1)

### Accounts & subscriptions
- Interactive Brokers (5–10 days approval)
- IBKR US Securities Snapshot & Futures Value Bundle (~$10/mo, often waived if commissions ≥ $30/mo) — primary market data
- IBKR news subscription (Reuters / Briefing.com — free or low-cost tiers via TWS)
- Yahoo Finance (free, via `yfinance` or HTTP — used only for long-horizon daily backtests and the RS-vs-SPY filter)
- Azure subscription — VM (Standard B2s or larger, Ubuntu 22.04 LTS, East US 2 region for proximity to NYSE/Nasdaq)
- Telegram bot via BotFather

### Toolchain
- Python 3.12+, `uv` for dependency / venv management, Git
- **Core libs:** `ib_async` (IBKR TWS wrapper, async), `pandas` + `pyarrow` (data + Parquet), `yfinance` (Yahoo fallback), `pydantic-settings` (typed `.env` config), `structlog` (JSON-line logs), `python-telegram-bot` (alerts), `sqlite3` (stdlib, no ORM)
- **Dev tooling:** `pytest`, `pytest-asyncio`, `ruff` (lint + format), `mypy --strict` (type checking — non-negotiable since the same `Strategy` class must work against both the simulator and live IBKR)
- systemd unit hardening (OpenClaw + bot isolation via dedicated Linux users — same VM, no Docker)
- Azure NSG (network security group) for egress allowlisting

**Why Python, not C++:** ORB decides on 1-minute bars. The full broker round-trip (Azure East US 2 → IBKR → fill confirmation) is 20–60 ms. Python interpreter overhead per event is ~1–5 ms — invisible at this timescale. C++ would save <0.1% of decision latency and cost weeks of iteration speed and library ecosystem. C++ is the right call for HFT/market-making/co-located strategies; ORB is none of those.

---

## Phase 1 — Data Layer (Week 2)

- `MarketDataFeed` Protocol (`typing.Protocol`) — single contract for live and historical; structural typing so backtest and live adapters never inherit from a shared base, only conform to the interface
- `LiveFeed` — IBKR streaming via `ib_async` (`ib.reqMktData()` / `ib.reqRealTimeBars()` wrapped in async event handlers, yielded into the bot's main `asyncio` loop)
- `HistoricalFeed` — IBKR `ib.reqHistoricalData()` for intraday; `yfinance` for long-horizon daily and SPY benchmark; outputs cached to local Parquet via `pandas.DataFrame.to_parquet`
- **Pacing-aware request queue** — IBKR enforces ~60 historical requests / 10 min and 50 concurrent open requests. Use `asyncio.Semaphore(50)` plus a token-bucket rate limiter; the queue must back off and retry, never burst
- Bar aggregator: ticks → 1-min bars (cross-check against IBKR-supplied 1-min bars for sanity)
- Symbol metadata refresh nightly (float, ADV) via IBKR contract details + Yahoo fallback
- 3+ years minute data, splits/dividends adjusted, **includes delisted symbols** (note: IBKR historical excludes some delisted names — supplement with cached Yahoo or a one-time vendor dump)

**Deliverable:** Replay **3 years** of a representative universe ticker (small/mid-cap in the $5–$100 band with float <100M — e.g., SOFI, RIVN, or a low-float biotech) through your feed, and verify bar accuracy against a second source. Mega-caps like AAPL are explicitly **not** sufficient — the strategy never trades them, and their clean tape hides bugs that surface on thinner names (halts, gaps, sub-penny prints, after-hours sparseness).

---

## Phase 2 — Backtest Engine (Week 3)

Event-driven, single-threaded, deterministic.

- `EventLoop` — consumes bars chronologically (synchronous iterator for backtest; `async for` over `ib_async` events for live)
- `Strategy` Protocol — `on_bar(bar)`, `on_fill(fill)`, `on_timer(now)` (snake_case per PEP 8; `mypy --strict` enforces signature compatibility across backtest and live)
- `Portfolio` — positions, cash, P&L (immutable updates via `@dataclass(frozen=True)` + replace; cheap, deterministic, easy to snapshot)
- `OrderManager` — order lifecycle state machine
- `ExecutionSimulator` — slippage (0.05–0.15%), commissions ($0.005/share), partial fills

**Critical:** same `Strategy` class runs backtest AND live — only the feed and order router differ. Dependency injection at construction time, no global state, no `if backtest: ... else: ...` branches inside strategy logic.

**Deliverable:** Buy-and-hold dummy strategy produces correct P&L; `mypy --strict` passes; the same class is exercised in both backtest and a live-paper smoke test.

---

## Phase 3 — Scanner (Week 4)

Daily 9:00 ET universe builder, outputs ranked candidate list. Backtestable on historical pre-market data.

**Deliverable:** Scanner output for past dates matches manual checks.

---

## Phase 4 — ORB Strategy Implementation (Week 5)

Implement full strategy spec above.

### Trade Database Schema (`trades.db`)

```sql
CREATE TABLE trades (
  trade_id TEXT PRIMARY KEY,
  symbol TEXT,
  side TEXT,                  -- 'LONG' or 'SHORT'
  entry_time DATETIME,
  entry_price REAL,
  stop_price REAL,
  target_price REAL,
  exit_time DATETIME,
  exit_price REAL,
  exit_reason TEXT,           -- target, stop, time_stop, eod
  shares INTEGER,
  risk_dollars REAL,
  r_multiple REAL,
  pnl_dollars REAL,
  or_high REAL,
  or_low REAL,
  rel_volume REAL,
  rel_strength REAL,
  catalyst TEXT
);

CREATE TABLE signals (
  ts DATETIME,
  symbol TEXT,
  signal_type TEXT,
  passed BOOLEAN,
  filters_failed TEXT
);

CREATE TABLE bot_state (
  ts DATETIME,
  status TEXT,
  open_positions INTEGER,
  daily_pnl_r REAL,
  errors_count INTEGER
);
```

`bot.log`: JSON lines, one event per line, parseable by OpenClaw.

**Deliverable:** ORB runs on 6 months historical data, trade log matches manual sample-day checks.

---

## Phase 5 — Backtest Validation (Week 6)

- 60% in-sample tuning / 20% out-of-sample test (one shot) / 20% reserved
- Metrics: win rate, expectancy, Sharpe (>1.5), profit factor (>1.5), max DD (<25%)
- Stress tests: remove top 10 trades, 2× slippage, yearly breakdowns
- Parameter sensitivity: ±20% on every knob
- Monte Carlo trade-order shuffle (worst 5% equity curves)

**Go/no-go:** OOS within 30% of in-sample. If yes, proceed. If no, strategy is overfit or dead.

### The Seven Backtesting Sins (avoid all)

1. Look-ahead bias
2. Survivorship bias
3. Slippage too low
4. Liquidity assumption (size > 1% of bar volume)
5. Overfitting
6. Ignoring fees
7. Cherry-picked time period

---

## Phase 6 — Live Infrastructure (Week 7)

- IBKR TWS API via `ib_async` (the bot connects to a local IB Gateway or TWS instance over TCP)
- Live data feed adapter conforming to `MarketDataFeed` Protocol
- Live order router conforming to `OrderManager`
- Reconciliation: bot-vs-broker positions/orders/cash every 60s
- Heartbeat: bot writes timestamp every 30s
- **Kill switch:** file-watcher (`/tmp/KILL_BOT`) flattens all and halts
- Pre-trade checks: every order validated against hard caps

**Deliverable:** Bot connects to IBKR paper account, places orders, reconciles cleanly.

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

1. `bot_reader` — custom skill, reads `trades.db`, `bot.log`, and the bot's cached news table
2. `news_brief` — formats per-ticker news headlines (sourced upstream by the Python bot via IBKR news API; OpenClaw only reads the cache)
3. `telegram_messenger` — formatted alerts (Telegram is the **only** notification channel — no email, no SMS)
4. `scheduler` — Heartbeat Engine for time-based triggers
5. `report_generator` — daily/weekly summaries

### Custom Skill: `bot_reader/SKILL.md`

```markdown
# Bot Reader Skill

Reads Python ORB bot's trade database and structured logs.

## Tools
- get_today_trades() → list of trades placed today
- get_open_positions() → current positions and unrealized P&L
- get_bot_status() → last heartbeat, errors, daily P&L in R
- get_recent_signals(n) → last N signal evaluations with pass/fail
- query_trades(start, end, filters) → historical trade query

## Constraints
- READ ONLY. Never write. Never call broker APIs.
- Never recommend acting on data without human in loop.
- If bot status shows errors, alert immediately via Telegram.
```

---

## Phase 8 — Scheduled Workflows (Week 9)

### 08:00 ET — Morning Brief

OpenClaw:
1. Read scanner's overnight candidate list
2. Read last-24h news per ticker from the bot's cached news table (populated by IBKR news subscription)
3. Tag catalysts: earnings / guidance / FDA / analyst / macro
4. Check macro calendar (CPI, FOMC, Fed speakers — dedicated calendar source TBD; not part of IBKR)
5. Send to Telegram

```
🌅 Morning Brief — Apr 30
Macro: ⚠️ CPI 8:30 ET — bot will skip pre-10:30 entries

Top candidates:
🔹 NVDA  +4.2% gap | earnings beat
🔹 PLTR  +3.1% gap | analyst upgrade
🔹 SOFI  +5.8% gap | guidance raise
🔹 RIVN  -3.2% gap | delivery miss

No earnings in candidate list today: ✅
Bot status: ready, last heartbeat 07:59
```

### Real-time — Fill Alerts

```
✅ FILLED: LONG NVDA
Entry: 845.30 | Stop: 838.10 | Target: 866.90
Risk: $720 (0.8%) | Catalyst: earnings beat
OR: 842.50-845.20 | RelVol: 3.2x | RS: +0.4%
```

### Real-time — Anomaly Alerts

Triggers:
- Error count >2 in 10 min
- Heartbeat gap >2 min
- Daily loss approaching limit
- Slippage >2× backtest

### 16:30 ET — End-of-Day Report

```
📊 EOD — Apr 30
Trades: 4 | Wins: 2 | Losses: 2
Total R: +0.8R | $720 net
Avg slippage: 0.08% (backtest: 0.10%) ✅

Best: NVDA +3.0R (target hit)
Worst: PLTR -1.0R (stop hit, no follow-through)

Pattern note: Both losses occurred 10:30-11:00 — late breakouts
underperforming this week. Consider tightening time window.

Bot health: 0 errors, 100% uptime
```

### Friday 17:00 — Weekly Review

- Rolling 30-trade win rate, expectancy, R distribution
- Live vs backtest comparison
- Pattern detection (best/worst times, symbols, conditions)
- Drawdown / streak status
- Recommendations: continue / tweak / pause

### On-Demand via Telegram

- "Status?" → bot health + open positions + daily R
- "Why no trades today?" → reads signals table, lists failed filters
- "Show last 10 trades" → table with R-multiples
- "Pause bot" → OpenClaw replies: *"I can't do that — touch the kill file or use IBKR."*

---

## Phase 9 — Paper Trading (Week 10)

Both systems running. Python bot trades paper, OpenClaw assists.

### Validation criteria
- Paper performance within 30% of backtest expectation
- All OpenClaw alerts firing correctly, no false positives
- No bot crashes or unexplained errors
- 20+ trading days observed and trusted

---

## Phase 10 — Live, Small Size (Week 11)

- Start at 1/10 intended size (0.1% risk per trade)
- 30+ live trades minimum before scaling
- Compare live R-distribution to backtest
- Scale: 2× → 4× → full size, 20+ trades between each step

---

## Phase 11 — Operations (Week 12+)

- **Daily:** glance at OpenClaw EOD brief
- **Weekly:** review pattern report → continue / tweak / pause
- **Monthly:** parameter drift, regime check
- **Quarterly:** full re-backtest on recent data

### Pause Triggers (OpenClaw alerts)
- Rolling 30-trade expectancy < 0
- 8+ losing streak
- Live slippage 2×+ backtest for >2 weeks
- Win rate drops below 28% rolling

---

## Hard Rules — Never Break

1. OpenClaw never gets broker credentials. Ever.
2. OpenClaw never writes to bot's DB or config.
3. Kill switch is a file or web endpoint, not a chat command.
4. Every code path live = code path that ran in backtest.
5. Trading bot has zero LLM dependencies in hot path. The Python decision loop is `ib_async` events + pure Python logic — no API calls to Anthropic, OpenClaw, or any external model.
6. Run OpenClaw under a dedicated unprivileged Linux user with systemd hardening; egress restricted to Telegram + Anthropic via Azure NSG. IBKR credentials live in a directory the `openclaw` user has no permission to read.
7. Audit OpenClaw outputs weekly — LLMs hallucinate.

---

## Timeline (Aggressive — 1 Week Per Phase)

| Phase | Duration | Cumulative |
|---|---|---|
| 0 — Foundation | 1 wk | 1 wk |
| 1 — Data layer | 1 wk | 2 wk |
| 2 — Backtest engine | 1 wk | 3 wk |
| 3 — Scanner | 1 wk | 4 wk |
| 4 — ORB strategy | 1 wk | 5 wk |
| 5 — Validation | 1 wk | 6 wk |
| 6 — Live infra | 1 wk | 7 wk |
| 7 — OpenClaw setup | 1 wk | 8 wk |
| 8 — Workflows | 1 wk | 9 wk |
| 9 — Paper trading | 1 wk | 10 wk |
| 10 — Live small | 1 wk | 11 wk |
| 11 — Operations | ongoing | wk 12+ |

**~11 weeks to live capital.** This is an aggressive plan, not a realistic one.

### Where 1 week breaks down

- **Phase 9 (Paper Trading):** the validation criteria call for "20+ trading days observed." A 1-week phase gives ~5 trading days and 5–15 trades — not enough samples to detect overfitting, slippage drift, or operational bugs that only surface on slow days. **If you compress this, you're trading on faith.**
- **Phase 10 (Live, Small Size):** "30+ live trades minimum before scaling" cannot fit in 5 trading days at this strategy's frequency (1–3 trades/day target). Honest minimum: 3–4 weeks.
- **Phase 2 (Backtest Engine) and Phase 6 (Live Infrastructure):** historically the two phases that slip most. Event loop correctness and broker reconciliation are where bugs hide. Plan for slippage.

Treat this timeline as the **aspirational floor**. If a phase blows past 1 week, the right move is to extend, not to skip the deliverable.

---

## This Week's Action Items

1. Open IBKR account; enable US Securities Snapshot & Futures Value Bundle market data and a news subscription (Reuters or Briefing.com tier)
2. Provision Azure VM (Standard B2s, Ubuntu 22.04 LTS, East US 2); harden SSH, create `orb-bot` and `openclaw` system users with the `/opt/...` filesystem layout from Phase 7, install Python 3.12+ and `uv`, configure NSG egress allowlist
3. Create Telegram bot via BotFather, save token to Azure Key Vault (or `.env` for dev)
4. Skim `ib_async` README and IBKR TWS API pacing rules — focus on `reqHistoricalData` semantics and the ~60 requests / 10 min ceiling
5. Bootstrap the project: `uv init`, add deps (`ib_async`, `pandas`, `pyarrow`, `yfinance`, `pydantic-settings`, `structlog`, `python-telegram-bot`, `pytest`, `ruff`, `mypy`), then start Phase 1 with the pacing-aware request queue first

---

## Common Failure Points

- Skipping paper trading because backtest looks good → loses on day 1
- Building everything custom → use libraries
- Optimizing for speed before correctness → get it right first
- Trading live before reconciliation works → bot/broker disagree, chaos
- No kill switch → first bad day wipes you out
- Not logging everything → impossible to debug
- Letting OpenClaw scope-creep into execution path → ruins latency, adds hallucination risk

---

## The One Rule That Matters Most

**Never let the live bot do something the backtest didn't simulate.**
Every live code path must have run in backtest first. This catches 90% of disasters.

---