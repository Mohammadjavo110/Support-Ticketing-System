"""Environment configuration, read once at import.

The API key is only ever used for the upstream Authorization header; it is
never returned in a response and never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    provider: str
    api_url: str
    api_key: str
    model: str
    min_confidence: float
    timeout_seconds: float

    @property
    def mode(self) -> str:
        # Hosted only when the upstream is an external https endpoint AND a
        # key exists. A plain http URL (e.g. the compose-internal one) with
        # or without a key always runs the local rules engine, so a
        # self-referencing URL can never create a request loop.
        if self.api_url.lower().startswith("https://") and self.api_key:
            return "hosted"
        return "local"


def load_settings() -> Settings:
    return Settings(
        provider=os.getenv("ENGINE_PROVIDER", "laya").strip() or "laya",
        api_url=os.getenv("LAYA_API_URL", "").strip(),
        api_key=os.getenv("LAYA_API_KEY", "").strip(),
        model=os.getenv("LAYA_MODEL", "").strip() or "laya-multilingual",
        min_confidence=min(1.0, max(0.0, _float_env("ENGINE_MIN_CONFIDENCE", 0.55))),
        timeout_seconds=min(60.0, max(0.1, _float_env("ENGINE_TIMEOUT_SECONDS", 8.0))),
    )
