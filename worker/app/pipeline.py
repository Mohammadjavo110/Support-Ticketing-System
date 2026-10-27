"""The whole triage job for one ticket.

Writes only ai_suggestions and ai_runs (worker owns those two tables);
tickets and messages are read-only for this service.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

from . import drafter
from .config import Settings
from .engine import Decision, EngineUnavailable, apply_confidence, build_engine

logger = logging.getLogger(__name__)

MARK_RUNNING = """
update ai_runs
   set status = 'running', started_at = now(), last_error = null, attempt = $2
 where id = (
     select id from ai_runs
      where ticket_id = $1 and status in ('queued', 'running')
      order by created_at desc
      limit 1
 )
returning id
"""

INSERT_RUNNING = """
insert into ai_runs (ticket_id, status, attempt, started_at)
values ($1, 'running', $2, now())
returning id
"""

READ_TICKET = "select body from tickets where id = $1"

RECORD_ERROR = "update ai_runs set last_error = $2 where id = $1"

# One row per ticket: insert the decision, refresh it on retry, keep draft_text.
UPSERT_SUGGESTION = """
insert into ai_suggestions
    (ticket_id, category, category_confidence, priority, priority_confidence,
     needs_manual_triage, model_name, updated_at)
values ($1, $2, $3, $4, $5, $6, $7, now())
on conflict (ticket_id) do update
   set category = excluded.category,
       category_confidence = excluded.category_confidence,
       priority = excluded.priority,
       priority_confidence = excluded.priority_confidence,
       needs_manual_triage = excluded.needs_manual_triage,
       model_name = excluded.model_name,
       updated_at = now()
returning draft_status
"""

UPDATE_DRAFT = """
update ai_suggestions
   set draft_text = $2, draft_status = $3, updated_at = now()
 where ticket_id = $1
"""

_LATENCY_SQL = (
    "(floor(extract(epoch from (now() - coalesce(started_at, created_at))) * 1000))::int"
)

FINISH_RUN = f"""
update ai_runs
   set status = $2, finished_at = now(), model_name = $3,
       latency_ms = {_LATENCY_SQL}
 where id = $1
"""

FAIL_RUN = f"""
update ai_runs
   set status = 'failed', last_error = $2, attempt = $3, finished_at = now(),
       latency_ms = {_LATENCY_SQL}
 where id = (
     select id from ai_runs
      where ticket_id = $1 and status in ('queued', 'running')
      order by created_at desc
      limit 1
 )
"""


@dataclass
class JobResult:
    status: str
    retryable: bool = False
    error: str | None = None
    error_class: str | None = None


def _ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


async def _mark_running(pool: asyncpg.Pool, ticket_id: str, attempt: int) -> str | None:
    row = await pool.fetchrow(MARK_RUNNING, ticket_id, attempt)
    if row is not None:
        return row["id"]
    try:
        row = await pool.fetchrow(INSERT_RUNNING, ticket_id, attempt)
    except asyncpg.exceptions.ForeignKeyViolationError:
        # No tickets row means there is nothing to audit against.
        return None
    return row["id"] if row is not None else None


async def mark_failed(pool: asyncpg.Pool, ticket_id: str, error: str, attempts: int) -> None:
    """Terminal failure after retries are exhausted (called by the consumer)."""
    await pool.execute(FAIL_RUN, ticket_id, error[:500], attempts)


async def process_ticket(ticket_id: str, ctx: dict[str, Any]) -> JobResult:
    """Run the full pipeline for one queued ticket. Never raises (except cancel)."""
    started = time.monotonic()
    try:
        uuid.UUID(ticket_id)
    except (TypeError, ValueError):
        logger.warning("queue job rejected reason=invalid_ticket_id")
        return JobResult("invalid")

    settings: Settings = ctx["settings"]
    pool: asyncpg.Pool = ctx["pool"]
    attempt = int(ctx.get("attempt", 1))

    try:
        run_id = await _mark_running(pool, ticket_id, attempt)
        if run_id is None:
            logger.warning("run aborted reason=ticket_missing ticket_id=%s", ticket_id)
            return JobResult("missing")

        row = await pool.fetchrow(READ_TICKET, ticket_id)
        if row is None:
            logger.warning("run aborted reason=ticket_missing ticket_id=%s", ticket_id)
            return JobResult("missing")
        text = row["body"]

        engine = build_engine(settings, ctx.get("http"))
        engine_error: str | None = None
        try:
            decision = await engine.decide(text)
        except EngineUnavailable as exc:
            decision = Decision(
                category="unknown",
                category_confidence=0.0,
                priority=2,
                priority_confidence=0.0,
                needs_manual_triage=True,
            )
            engine_error = str(exc)[:500]

        decision = apply_confidence(decision, settings.engine_min_confidence)

        suggestion = await pool.fetchrow(
            UPSERT_SUGGESTION,
            ticket_id,
            decision.category,
            float(decision.category_confidence),
            int(decision.priority),
            float(decision.priority_confidence),
            bool(decision.needs_manual_triage),
            engine.model_name,
        )

        if engine_error is not None:
            # Soft-fail: abstained decision is stored, the consumer retries and
            # eventually marks the run failed with this last_error.
            await pool.execute(RECORD_ERROR, run_id, engine_error)
            logger.warning(
                "decision unavailable class=EngineUnavailable ticket_id=%s run_id=%s duration_ms=%d",
                ticket_id,
                run_id,
                _ms(started),
            )
            return JobResult(
                "engine_unavailable",
                retryable=True,
                error=engine_error,
                error_class="EngineUnavailable",
            )

        if suggestion is not None and suggestion["draft_status"] == "ready":
            # Idempotency: a ready draft already exists, never call the LLM twice.
            await pool.execute(FINISH_RUN, run_id, "succeeded", engine.model_name)
            logger.info(
                "run finished status=succeeded idempotent=true ticket_id=%s run_id=%s duration_ms=%d",
                ticket_id,
                run_id,
                _ms(started),
            )
            return JobResult("succeeded")

        draft_text, draft_status = await drafter.draft(
            ticket_id, text, decision.category, ctx["http"]
        )
        await pool.execute(UPDATE_DRAFT, ticket_id, draft_text, draft_status)

        run_status = "succeeded" if draft_status == "ready" else "succeeded_no_draft"
        await pool.execute(FINISH_RUN, run_id, run_status, engine.model_name)
        logger.info(
            "run finished status=%s ticket_id=%s run_id=%s duration_ms=%d",
            run_status,
            ticket_id,
            run_id,
            _ms(started),
        )
        return JobResult(run_status)

    except Exception as exc:
        logger.error(
            "run failed class=%s ticket_id=%s duration_ms=%d",
            type(exc).__name__,
            ticket_id,
            _ms(started),
        )
        try:
            await mark_failed(pool, ticket_id, str(exc)[:500], attempt)
        except Exception as inner:
            logger.error(
                "failure not recorded class=%s ticket_id=%s",
                type(inner).__name__,
                ticket_id,
            )
        return JobResult("failed", error=str(exc)[:500], error_class=type(exc).__name__)
