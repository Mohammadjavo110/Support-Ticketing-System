"""Hosted mode: thin adapter over an external model API.

Forwards the typed questions with a bearer token and normalizes whatever
comes back into the internal answer shape. Tolerant of the common upstream
shapes (nested "answers", flat object, or a list of {id, value, confidence}).
"""

from __future__ import annotations

import logging

import httpx

from .config import Settings

log = logging.getLogger("laya.serve.hosted")

_DEFAULT_KEYS = ("answers", "predictions", "results", "output")


class UpstreamError(Exception):
    """Upstream timed out, was unreachable, or answered unusably."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


async def predict(
    settings: Settings, text: str, questions: list[dict], model: str
) -> dict[str, dict]:
    payload = {"text": text, "questions": questions, "model": model}
    headers = {"Authorization": f"Bearer {settings.api_key}"}
    try:
        async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
            response = await client.post(settings.api_url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise UpstreamError(
            "upstream_timeout", f"upstream did not answer within {settings.timeout_seconds:g}s"
        ) from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(
            "upstream_unreachable", f"upstream unreachable ({type(exc).__name__})"
        ) from exc

    if response.status_code >= 400:
        raise UpstreamError("upstream_http_error", f"upstream returned HTTP {response.status_code}")

    try:
        data = response.json()
    except ValueError as exc:
        raise UpstreamError("upstream_bad_response", "upstream returned a non-JSON body") from exc

    return normalize(data, questions)


def normalize(data: object, questions: list[dict]) -> dict[str, dict]:
    ids = {q["id"] for q in questions}
    raw = _extract(data, ids)
    if not raw:
        raise UpstreamError("upstream_bad_response", "upstream response had no answers")

    answers: dict[str, dict] = {}
    for q in questions:
        parsed = _parse(q, raw.get(q["id"])) if q["id"] in raw else None
        if parsed is None:
            # Keep the contract shape; zero confidence tells the worker to abstain.
            parsed = {"value": _default(q), "confidence": 0.0}
        answers[q["id"]] = parsed
    return answers


def _extract(data: object, ids: set[str]) -> dict | None:
    if isinstance(data, dict):
        for key in _DEFAULT_KEYS:
            block = data.get(key)
            if isinstance(block, dict):
                return {k: v for k, v in block.items() if k in ids}
            if isinstance(block, list):
                return _from_list(block, ids)
        flat = {k: v for k, v in data.items() if k in ids}
        return flat or None
    if isinstance(data, list):
        return _from_list(data, ids)
    return None


def _from_list(items: list, ids: set[str]) -> dict:
    out = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = item.get("id", item.get("question_id"))
        if qid in ids:
            out[qid] = item
    return out


def _parse(q: dict, item: object) -> dict | None:
    if isinstance(item, dict):
        value = item.get("value", item.get("answer"))
        confidence = item.get("confidence", 0.0)
    else:
        value, confidence = item, 0.0
    if value is None:
        return None
    try:
        value = _coerce(q, value)
    except (TypeError, ValueError):
        return None
    try:
        confidence = float(confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        confidence = 0.0
    return {"value": value, "confidence": max(0.0, min(1.0, confidence))}


def _coerce(q: dict, value: object) -> object:
    qtype = q["type"]
    if qtype == "score":
        number = int(value)  # type: ignore[arg-type]
        return max(q["min"], min(q["max"], number))
    if qtype == "noul":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in ("true", "yes", "y", "1"):
            return True
        if text in ("false", "no", "n", "0"):
            return False
        raise ValueError(text)
    text = str(value).strip()
    for option in q.get("options") or []:
        if option.lower() == text.lower():
            return option  # canonical casing from the question's option list
    return text


def _default(q: dict) -> object:
    if q["type"] == "score":
        return q["min"] + (q["max"] - q["min"]) // 2
    if q["type"] == "noul":
        return True  # safe default: prefer a human look over a silent skip
    options = q.get("options") or []
    return next((o for o in options if o.lower() in ("other", "unknown")), options[0] if options else "")
