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

READ_TICKET = "select subject, body from tickets where id = $1"

# The engine and the drafter work on the whole conversation, not just the
# original body, so a follow-up customer message gets a matching draft.
READ_MESSAGES = """
select direction, body, created_at from messages
  where ticket_id = $1
  order by created_at asc, id asc
"""

READ_EXISTING = "select draft_status, updated_at from ai_suggestions where ticket_id = $1"

# A ready draft that predates the customer's newest message must stop being
# offered the moment the follow-up run starts; it is regenerated below.
INVALIDATE_DRAFT = """
update ai_suggestions
   set draft_text = null, draft_status = 'pending'
 where ticket_id = $1
   and draft_status = 'ready'
"""

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


def _thread_text(subject: str, body: str, messages: list[asyncpg.Record]) -> str:
    """Flatten the ticket into one text: subject, original body, every message.

    The customer's newest message comes last, which is what the drafter must
    answer; agent messages are included as context only.
    """
    parts = [f"Ticket subject: {subject}", body]
    for record in messages:
        who = "Customer" if record["direction"] == "in" else "Support agent"
        parts.append(f"{who}: {record['body']}")
    return "\n\n".join(parts)


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
        messages = await pool.fetch(READ_MESSAGES, ticket_id)
        text = _thread_text(row["subject"], row["body"], messages)

        # Ascending order: the last `in` record is the customer's newest
        # message — the one a suggestion must postdate to stay valid.
        last_inbound: Any = None
        for record in messages:
            if record["direction"] == "in":
                last_inbound = record["created_at"]

        existing = await pool.fetchrow(READ_EXISTING, ticket_id)
        # Reuse a ready draft only while no customer message is newer than it.
        # Everything else (no row, pending/failed draft, stale ready draft) is
        # regenerated from the full thread below.
        draft_is_current = bool(
            existing is not None
            and existing["draft_status"] == "ready"
            and (last_inbound is None or last_inbound <= existing["updated_at"])
        )
        if (
            existing is not None
            and existing["draft_status"] == "ready"
            and not draft_is_current
        ):
            # Drop the stale draft before the (slow) model calls, so the UI
            # stops offering the previous suggestion right away.
            await pool.execute(INVALIDATE_DRAFT, ticket_id)
            logger.info(
                "stale draft invalidated ticket_id=%s run_id=%s", ticket_id, run_id
            )

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

        # The upsert refreshes the decision fields (and updated_at); draft
        # fields are only touched by INVALIDATE_DRAFT / UPDATE_DRAFT.
        await pool.execute(
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

        if draft_is_current:
            # Idempotency: a current ready draft already exists (no newer
            # customer message), never call the LLM twice.
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
