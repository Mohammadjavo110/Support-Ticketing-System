"""Environment-driven settings for the worker service.

All values are read from the process environment (docker-compose passes .env
plus DATABASE_URL / REDIS_URL). Nothing here is ever logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_QUEUE_KEY = "triage:queue"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def _text(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw or default


def _num(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    engine_provider: str
    laya_api_url: str
    laya_api_key: str
    laya_model: str
    engine_min_confidence: float
    engine_timeout_seconds: float
    llm_api_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: float
    queue_key: str


def get_settings() -> Settings:
    return Settings(
        database_url=_text("DATABASE_URL"),
        redis_url=_text("REDIS_URL", DEFAULT_REDIS_URL),
        engine_provider=_text("ENGINE_PROVIDER", "rules").lower(),
        laya_api_url=_text("LAYA_API_URL"),
        laya_api_key=_text("LAYA_API_KEY"),
        laya_model=_text("LAYA_MODEL", "laya-multilingual"),
        engine_min_confidence=_num("ENGINE_MIN_CONFIDENCE", 0.55),
        engine_timeout_seconds=_num("ENGINE_TIMEOUT_SECONDS", 8.0),
        llm_api_url=_text("LLM_API_URL"),
        llm_api_key=_text("LLM_API_KEY"),
        llm_model=_text("LLM_MODEL"),
        llm_timeout_seconds=_num("LLM_TIMEOUT_SECONDS", 20.0),
        queue_key=_text("QUEUE_KEY", DEFAULT_QUEUE_KEY),
    )


def asyncpg_dsn(database_url: str) -> str:
    """Strip the SQLAlchemy driver suffix so asyncpg accepts the DSN.

    docker-compose hands out ``postgresql+asyncpg://...``; asyncpg speaks the
    plain libpq scheme (``postgresql://...``).
    """
    if "+asyncpg" in database_url:
        scheme, sep, rest = database_url.partition("://")
        return scheme.split("+", 1)[0] + sep + rest
    return database_url
