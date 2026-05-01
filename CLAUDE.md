# CLAUDE.md (trading-bot project)

## Package Manager Policy — STRICT

| Tool | Status |
|------|--------|
| `uv` | **REQUIRED** for all dependency installation, venv management, Python version pinning |
| `pip` | **FORBIDDEN** — even inside Docker containers. Use `uv pip install` or pre-built images |
| `pip install` in Dockerfile | **FORBIDDEN** — use `uv` in Dockerfile (`pip install uv` then `uv pip install ...`) |
| `poetry`, `conda`, `pipenv`, `pyenv` | **FORBIDDEN** — `uv` replaces all of them |

**Rationale:** `uv` is 10-100× faster than `pip`, produces reproducible builds via `uv.lock`, handles Python version installation, and is the de facto Python toolchain standard going into 2026.

## Bootstrap commands

```bash
# Initialize project
uv init --no-readme
uv python pin 3.14
uv add ib_async pandas pyarrow yfinance pydantic-settings structlog python-telegram-bot
uv add --dev pytest pytest-asyncio ruff mypy
uv sync

# Run code
uv run python script.py
uv run pytest
uv run ruff check
uv run mypy --strict src/
```

## Docker pattern (when containers are needed)

❌ **WRONG** — uses pip directly:
```dockerfile
FROM python:3.14-slim
RUN pip install yfinance pandas
```

❌ **WRONG** — uses pip in run command:
```bash
docker run python:3.14-slim bash -c 'pip install yfinance && python script.py'
```

✅ **RIGHT** — use uv-based Astral image, or install uv first:
```dockerfile
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY . .
CMD ["uv", "run", "python", "script.py"]
```

✅ **RIGHT** — for one-off containers:
```bash
docker run --rm ghcr.io/astral-sh/uv:python3.14-bookworm-slim \
    bash -c 'uv pip install --system ib_async pandas pyarrow && python /script.py'
```

## Stack reference

| Layer | Choice |
|---|---|
| Language | Python 3.14 |
| Package manager | uv |
| Broker | IBKR via `ib_async` (clientId=12, never 11 — that's ai-trader's) |
| Data storage | Parquet (bars) + SQLite (trades) — never both for same data |
| Deployment | Shared Azure VM with ai-trader (eastus2, B4ms) |
| Notifications | Telegram only (no email, no SMS) |
| Isolation | Docker on shared VM; OpenClaw with `read_only`, `cap_drop: [ALL]`, no secrets mount |

## Risk Management Standard — STRICT

**ALL backtests and live strategies must use this R:R unless explicitly overridden by the user with a clear reason:**

| Parameter | Value |
|---|---|
| **Stop loss** | **−6% from entry** |
| **Take profit (target)** | **+18% from entry** |
| **R:R ratio** | **1:3** (one unit of risk for three units of reward) |
| **Time stop** | 90 days max hold |
| **Position size** | $1,000 fixed per trade (or 1% of account if specified) |

**Math reference:**
- 1R = the stop distance = 6% of entry price
- Target = 3R = 18% of entry price
- Breakeven win rate at 1:3 = 25% (real-world ~30% after fees)

**Why this matters:** every strategy backtested with the SAME R:R is directly comparable. Different R:R values change win-rate math entirely (1:1 needs 50%+ win rate, 1:3 needs only 25%+).

**When writing or porting a strategy:**
- Default to STOP_PCT = 0.06, TARGET_PCT = 0.18, TIME_STOP_DAYS = 90
- If a strategy's source spec uses different numbers (e.g., Andrea's −30%/+30%), ASK USER before deviating, OR include both versions side-by-side for comparison
- Never silently use other R:R values

## See also

- `README.md` — full project plan, 12 phases
- `infra/SHARED.md` (gitignored) — VM access details, never committed
- Global rules: `~/.claude/CLAUDE.md`
