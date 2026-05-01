"""Centralized config — paths, DB location, scanner thresholds.

Reads env vars with `TB_` prefix. Override anything in production via env.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TB_", env_file=".env", extra="ignore")

    bars_dir: Path = Path("/data/bars")
    db_path: Path = Path("./data/signals.db")

    min_price: float = 10.0
    max_price: float = 200.0
    min_adv: int = 500_000
    base_min_weeks: int = 5
    base_max_weeks: int = 15
    base_min_depth: float = 0.10
    base_max_depth: float = 0.25
    vcp_min_pullbacks: int = 3
    volume_multiplier: float = 1.5
    max_positions: int = 10
    fixed_dollar_per_trade: float = 1000.0
    stop_pct: float = 0.10
    target_pct: float = 0.30


settings = Settings()
