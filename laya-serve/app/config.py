"""Environment configuration, read once at import.

Two different URLs exist on purpose and must not be confused:

* ``LAYA_API_URL`` is the *worker's* target — where the worker POSTs
  ``/predict``. Inside compose that is the internal
  ``http://laya-serve:8000/predict``.
* ``LAYA_UPSTREAM_URL`` / ``LAYA_UPSTREAM_KEY`` are *laya-serve's* upstream —
  an external model API called only in hosted mode. (The old single-variable
  setup, an https ``LAYA_API_URL`` plus ``LAYA_API_KEY``, is still honoured as
  a fallback so existing environments keep working.)

The API key is only ever used for the upstream Authorization header; it is
never returned in a response and never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _text_env(name: str) -> str:
    return os.getenv(name, "").strip()


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

    @property
    def upstream_style(self) -> str:
        # An OpenAI-compatible gateway is addressed by its chat endpoint and
        # speaks {model, messages}; anything else gets the legacy
        # {text, questions} contract of the original Laya API.
        return "chat" if "/chat/completions" in self.api_url.lower() else "laya"


def load_settings() -> Settings:
    upstream_url = _text_env("LAYA_UPSTREAM_URL")
    upstream_key = _text_env("LAYA_UPSTREAM_KEY")
    if not upstream_url and _text_env("LAYA_API_URL").lower().startswith("https://"):
        # Legacy single-variable setup: only an external https URL may be
        # reinterpreted as an upstream (the compose-internal one is http and
        # can never leave local mode anyway — see ``mode``).
        upstream_url = _text_env("LAYA_API_URL")
        upstream_key = upstream_key or _text_env("LAYA_API_KEY")
    return Settings(
        provider=_text_env("ENGINE_PROVIDER") or "laya",
        api_url=upstream_url,
        api_key=upstream_key,
        model=_text_env("LAYA_MODEL") or "laya-multilingual",
        min_confidence=min(1.0, max(0.0, _float_env("ENGINE_MIN_CONFIDENCE", 0.55))),
        timeout_seconds=min(60.0, max(0.1, _float_env("ENGINE_TIMEOUT_SECONDS", 8.0))),
    )
