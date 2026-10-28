"""Unit tests for the customer-history helpers in the worker pipeline.

The worker is not run in this suite (no Postgres/Redis/LLM), but the pure
formatting helpers that build the drafter's history context are pinned here:
bounds (max tickets/chars), clipping, and the empty-history case.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
import types

WORKER_APP = pathlib.Path(__file__).resolve().parents[1] / "worker" / "app"


def _load(name: str):
    """Import worker/app/<name>.py under a private package to dodge the
    ``app`` name clash with the api package imported by conftest."""
    if "worker_app" not in sys.modules:
        pkg = types.ModuleType("worker_app")
        pkg.__path__ = [str(WORKER_APP)]
        sys.modules["worker_app"] = pkg
    full = f"worker_app.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, WORKER_APP / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


_load("config")
_load("engine")
_load("drafter")
pipeline = _load("pipeline")


class _Row(dict):
    """asyncpg.Record stand-in: attribute-style access is not needed here."""


def _ticket(subject: str, body: str, day: int, status: str = "closed") -> _Row:
    return _Row(
        subject=subject,
        body=body,
        status=status,
        created_at=dt.datetime(2026, 1, day, 12, 0, 0),
    )


def test_clip_collapses_whitespace():
    assert pipeline._clip("  a\n\tb  ", 100) == "a b"


def test_clip_truncates_with_ellipsis():
    clipped = pipeline._clip("x" * 500, 100)
    assert len(clipped) == 100
    assert clipped.endswith("…")


def test_empty_history_is_empty_string():
    assert pipeline._history_text([]) == ""


def test_history_renders_each_ticket_with_date_status_subject_body():
    text = pipeline._history_text(
        [_ticket("Submit failed", "body one", 2), _ticket("Refund", "body two", 1)]
    )
    lines = text.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("- 2026-01-02 [closed] Submit failed: body one")
    assert lines[1].startswith("- 2026-01-01 [closed] Refund: body two")


def test_history_caps_total_chars():
    big = [_ticket(f"subject {i}", "y" * 400, i) for i in range(1, 6)]
    text = pipeline._history_text(big)
    assert len(text) <= pipeline.HISTORY_MAX_CHARS
    assert text.count("\n") + 1 <= pipeline.HISTORY_MAX_TICKETS
