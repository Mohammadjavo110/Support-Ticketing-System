"""Stage 1: typed decision over ticket text.

Three interchangeable implementations behind one Protocol (MANUAL section 5):
RulesEngine (keyword baseline, no network), LayaEngine (HTTP to laya-serve)
and FakeEngine (tests). apply_confidence() is the soft-fail/abstention rule.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol

import httpx

from .config import Settings, get_settings

CATEGORY_ORDER = ("billing", "technical", "shipping", "account", "other")

# Persian + English keywords; matched against lowercased text.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "billing": (
        "invoice", "billing", "payment", "refund", "charged", "charge",
        "price", "cost", "subscription", "receipt",
        "\u067e\u0631\u062f\u0627\u062e\u062a", "\u0635\u0648\u0631\u062a\u062d\u0633\u0627\u0628",
        "\u0641\u0627\u0643\u062a\u0648\u0631", "\u0647\u0632\u06cc\u0646\u0647",
        "\u0628\u0627\u0632\u067e\u0631\u062f\u0627\u062e\u062a", "\u0642\u06cc\u0645\u062a",
        "\u0627\u0634\u062a\u0631\u0627\u06a9",
    ),
    "technical": (
        "error", "bug", "crash", "broken", "not working", "doesn't work",
        "failed", "failure", "timeout", "server", "install", "slow", "exception",
        "\u062e\u0637\u0627", "\u0628\u0627\u06af", "\u0627\u06cc\u0631\u0627\u062f",
        "\u062e\u0631\u0627\u0628", "\u06a9\u0631\u0634", "\u06a9\u0627\u0631 \u0646\u0645\u06cc\u06a9\u0646\u062f",
        "\u0645\u0634\u06a9\u0644 \u0641\u0646\u06cc", "\u0633\u0631\u0648\u0631", "\u06a9\u0646\u062f",
        "\u0646\u0635\u0628",
    ),
    "shipping": (
        "shipping", "delivery", "deliver", "shipment", "parcel", "tracking",
        "arrived", "arrive", "delayed", "dispatch",
        "\u0627\u0631\u0633\u0627\u0644", "\u062a\u062d\u0648\u06cc\u0644",
        "\u067e\u06cc\u06af\u06cc\u0631\u06cc", "\u0645\u0631\u0633\u0648\u0644\u0647",
        "\u0628\u0633\u062a\u0647", "\u0633\u0641\u0627\u0631\u0634",
    ),
    "account": (
        "account", "password", "login", "log in", "sign in", "register",
        "email", "username", "locked", "verify",
        "\u062d\u0633\u0627\u0628", "\u0631\u0645\u0632 \u0639\u0628\u0648\u0631",
        "\u0648\u0631\u0648\u062f", "\u062b\u0628\u062a \u0646\u0627\u0645", "\u062b\u0628\u062a\u200c\u0646\u0627\u0645",
        "\u0627\u06cc\u0645\u06cc\u0644", "\u0646\u0627\u0645 \u06a9\u0627\u0631\u0628\u0631\u06cc",
        "\u0642\u0641\u0644",
    ),
}

URGENCY_WORDS = (
    "urgent", "asap", "as soon as possible", "immediately", "right away",
    "critical", "emergency", "today",
    "\u0641\u0648\u0631\u06cc", "\u0641\u0648\u0631\u0627", "\u0641\u0648\u0631\u0627\u064b",
    "\u0628\u0647\u200c\u0633\u0631\u0639\u062a", "\u0628\u0633\u0631\u0639\u062a",
    "\u0633\u0631\u06cc\u0639", "\u062d\u06cc\u0627\u062a\u06cc",
)

LOW_PRIORITY_WORDS = (
    "low priority", "not urgent", "whenever", "no rush", "minor", "later",
    "\u063a\u06cc\u0631 \u0641\u0648\u0631\u06cc", "\u063a\u06cc\u0631\u0641\u0648\u0631\u06cc",
    "\u06a9\u0645\u200c\u0627\u0647\u0645\u06cc\u062a", "\u06a9\u0645 \u0627\u0647\u0645\u06cc\u062a",
    "\u0639\u062c\u0644\u0647 \u0646\u062f\u0627\u0631\u0645",
)

CONFIDENCE_HIT = 0.8
CONFIDENCE_FALLBACK = 0.3


@dataclass
class Decision:
    category: str
    category_confidence: float
    priority: int
    priority_confidence: float
    needs_manual_triage: bool


class EngineUnavailable(Exception):
    """The engine could not answer (HTTP/parse failure). Callers soft-fail."""


class DecisionEngine(Protocol):
    model_name: str

    async def decide(self, text: str) -> Decision: ...


def _count_hits(lowered: str, words: tuple[str, ...]) -> int:
    hits = 0
    for word in words:
        if word.isascii():
            if re.search(r"\b" + re.escape(word) + r"\b", lowered):
                hits += 1
        elif word in lowered:
            hits += 1
    return hits


class RulesEngine:
    """Keyword baseline: no network, no model.

    Confidence is 0.8 for a keyword hit and 0.3 for the fallback, so the
    fallback is deliberately below the abstention threshold (0.55 by default).
    """

    model_name = "rules"

    async def decide(self, text: str) -> Decision:
        lowered = text.lower()
        best_category, best_hits = "other", 0
        for category in ("billing", "technical", "shipping", "account"):
            hits = _count_hits(lowered, CATEGORY_KEYWORDS[category])
            if hits > best_hits:
                best_category, best_hits = category, hits
        category_confidence = CONFIDENCE_HIT if best_hits else CONFIDENCE_FALLBACK

        if _count_hits(lowered, URGENCY_WORDS):
            priority, priority_confidence = 1, CONFIDENCE_HIT
        elif _count_hits(lowered, LOW_PRIORITY_WORDS):
            priority, priority_confidence = 3, CONFIDENCE_HIT
        else:
            priority, priority_confidence = 2, CONFIDENCE_FALLBACK

        return Decision(best_category, category_confidence, priority, priority_confidence, False)


class FakeEngine:
    """Deterministic engine for tests: always returns the fixed Decision."""

    model_name = "fake"

    def __init__(self, decision: Decision) -> None:
        self._decision = decision

    async def decide(self, text: str) -> Decision:
        return self._decision


class LayaEngine:
    """HTTP client for the laya-serve ``/predict`` endpoint (typed questions).

    ``client`` is caller-owned (the shared httpx client from worker startup);
    when omitted the engine creates and closes its own per call.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self.model_name = settings.laya_model or "laya"

    async def decide(self, text: str) -> Decision:
        settings = self._settings
        if not settings.laya_api_url:
            raise EngineUnavailable("laya_url_not_configured")
        body = {
            "text": text,
            "questions": [
                {"id": "category", "type": "choice", "options": list(CATEGORY_ORDER)},
                {"id": "priority", "type": "score", "min": 1, "max": 3},
                {"id": "needs_review", "type": "noul"},
            ],
            "model": settings.laya_model,
        }
        headers = {}
        if settings.laya_api_key:
            # Required for a hosted (non-local) model.
            headers["Authorization"] = f"Bearer {settings.laya_api_key}"

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=settings.engine_timeout_seconds)
        try:
            try:
                response = await client.post(
                    settings.laya_api_url,
                    json=body,
                    headers=headers,
                    timeout=settings.engine_timeout_seconds,
                )
            except httpx.HTTPError as exc:
                raise EngineUnavailable(f"laya_request_failed:{type(exc).__name__}") from exc
            if response.status_code >= 400:
                # Status only; the body may echo ticket text.
                raise EngineUnavailable(f"laya_http_status:{response.status_code}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise EngineUnavailable("laya_bad_json") from exc
            return _parse_laya(payload)
        finally:
            if owns_client:
                await client.aclose()


def _answers_container(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise EngineUnavailable("laya_unrecognized_response")
    for key in ("answers", "results"):
        inner = payload.get(key)
        if isinstance(inner, Mapping):
            return inner
        if isinstance(inner, list):
            by_id: dict[str, Any] = {}
            for item in inner:
                if isinstance(item, Mapping) and "id" in item:
                    by_id[str(item["id"])] = item
            if by_id:
                return by_id
    return payload


def _field(container: Mapping[str, Any], key: str) -> tuple[Any, Any]:
    """Return (value, confidence) for one question id, tolerating shapes.

    Accepts ``{"category": "billing"}``, ``{"category": {"value": ...,
    "confidence": ...}}`` and flat ``{"category": ..., "category_confidence": ...}``.
    """
    raw = container.get(key)
    value: Any = None
    confidence: Any = None
    if isinstance(raw, Mapping):
        for value_key in ("value", "answer", "label", "prediction", "result"):
            if value_key in raw:
                value = raw[value_key]
                break
        for conf_key in ("confidence", "score", "prob", "probability"):
            if conf_key in raw:
                confidence = raw[conf_key]
                break
    elif raw is not None:
        value = raw
    if confidence is None:
        confidence = container.get(f"{key}_confidence", container.get(f"{key}_score"))
    return value, confidence


def _coerce_category(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text[:60] or None


def _coerce_priority(value: Any) -> int | None:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return min(3, max(1, number))


def _coerce_confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 1 < number <= 100:  # tolerate percentage-style scores
        number /= 100
    return min(1.0, max(0.0, number))


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1", "review", "manual"}
    return bool(value)


def _parse_laya(payload: Any) -> Decision:
    container = _answers_container(payload)
    if not any(key in container for key in ("category", "priority", "needs_review")):
        # Also accept aliases for the noul question.
        if not any(key in container for key in ("needs_manual_triage", "needs_human_review")):
            raise EngineUnavailable("laya_unrecognized_response")

    raw_category, raw_category_conf = _field(container, "category")
    category = _coerce_category(raw_category)
    category_confidence = _coerce_confidence(raw_category_conf)
    if category is None:
        category, category_confidence = "unknown", 0.0

    raw_priority, raw_priority_conf = _field(container, "priority")
    priority = _coerce_priority(raw_priority)
    priority_confidence = _coerce_confidence(raw_priority_conf)
    if priority is None:
        priority, priority_confidence = 2, 0.0

    review_value = None
    for review_key in ("needs_review", "needs_manual_triage", "needs_human_review"):
        if review_key in container:
            review_value, _ = _field(container, review_key)
            break

    return Decision(
        category=category,
        category_confidence=category_confidence if category_confidence is not None else 0.0,
        priority=priority,
        priority_confidence=priority_confidence if priority_confidence is not None else 0.0,
        needs_manual_triage=_coerce_bool(review_value),
    )


def apply_confidence(decision: Decision, threshold: float) -> Decision:
    """Abstention rule: below threshold, weaken the field and flag for a human.

    Original confidences are kept so the report can show how close the call was.
    """
    result = decision
    if result.category_confidence < threshold:
        result = replace(result, category="unknown", needs_manual_triage=True)
    if result.priority_confidence < threshold:
        result = replace(result, priority=2, needs_manual_triage=True)
    return result


def build_engine(
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> DecisionEngine:
    settings = settings or get_settings()
    provider = settings.engine_provider
    if provider == "rules":
        return RulesEngine()
    if provider == "laya":
        return LayaEngine(settings, client)
    if provider == "fake":
        return FakeEngine(
            Decision(
                category="other",
                category_confidence=0.9,
                priority=2,
                priority_confidence=0.9,
                needs_manual_triage=False,
            )
        )
    raise ValueError(f"unknown ENGINE_PROVIDER: {provider}")
