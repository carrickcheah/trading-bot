# trading-bot-py: VCP scanner + FastAPI on :8002
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
ENV TB_BARS_DIR=/data/bars
ENV TB_DB_PATH=/data/db/signals.db

EXPOSE 8002

CMD ["uvicorn", "trading_bot.api.main:app", "--host", "0.0.0.0", "--port", "8002"]
