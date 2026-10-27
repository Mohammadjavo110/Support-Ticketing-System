"""arq entrypoint: ``arq app.worker.WorkerSettings``.

Queue design
------------
The api producer LPUSHes ``{"ticket_id": ...}`` onto a plain Redis **list**
(``triage:queue``). arq's native queue is a sorted set populated via
``enqueue_job``, so arq's own job polling would never see those messages.
We therefore keep arq as the process host (startup/shutdown hooks, signal
handling, redis health) and consume the list ourselves with a dedicated
``BRPOP`` loop (LPUSH + BRPOP = FIFO). Retry semantics are implemented
manually: up to 3 attempts per job with exponential backoff, then the run is
marked ``failed``. A small passthrough task function is registered so arq gets
a non-empty function table (Worker.__init__ requires it) and anything enqueued
through arq's own API takes the identical execution path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import asyncpg
import httpx
import redis.asyncio as aioredis
from arq.connections import RedisSettings

from . import pipeline
from .config import asyncpg_dsn, get_settings
from .engine import build_engine

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 2.0  # 2s then 4s between attempts
BRPOP_TIMEOUT_SECONDS = 3   # bounds shutdown latency
POLL_ERROR_DELAY_SECONDS = 2.0


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    logging.getLogger("app").setLevel(logging.INFO)


_configure_logging()
logger = logging.getLogger(__name__)


def _ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


def _parse_ticket_id(raw: object) -> str | None:
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    ticket_id = payload.get("ticket_id")
    if not isinstance(ticket_id, str) or not ticket_id:
        return None
    return ticket_id


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set")
    build_engine(settings)  # fail fast on an invalid ENGINE_PROVIDER

    ctx["settings"] = settings
    ctx["pool"] = await asyncpg.create_pool(
        dsn=asyncpg_dsn(settings.database_url), min_size=2, max_size=10
    )
    ctx["http"] = httpx.AsyncClient()
    ctx["queue_redis"] = aioredis.from_url(settings.redis_url, decode_responses=True)
    ctx["consumer"] = asyncio.create_task(consume_loop(ctx))
    logger.info(
        "worker started provider=%s queue_key=%s",
        settings.engine_provider,
        settings.queue_key,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    consumer = ctx.pop("consumer", None)
    if consumer is not None:
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("consumer stop failed class=%s", type(exc).__name__)

    queue = ctx.pop("queue_redis", None)
    if queue is not None:
        try:
            closer = getattr(queue, "aclose", None) or queue.close
            await closer()
        except Exception as exc:
            logger.error("queue client close failed class=%s", type(exc).__name__)

    client = ctx.pop("http", None)
    if client is not None:
        try:
            await client.aclose()
        except Exception as exc:
            logger.error("http client close failed class=%s", type(exc).__name__)

    pool = ctx.pop("pool", None)
    if pool is not None:
        try:
            await pool.close()
        except Exception as exc:
            logger.error("db pool close failed class=%s", type(exc).__name__)

    logger.info("worker stopped")


async def consume_loop(ctx: dict[str, Any]) -> None:
    """BRPOP consumer for the producer's plain Redis list."""
    settings = ctx["settings"]
    queue = ctx["queue_redis"]
    while True:
        try:
            item = await queue.brpop(settings.queue_key, timeout=BRPOP_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("queue poll failed class=%s", type(exc).__name__)
            await asyncio.sleep(POLL_ERROR_DELAY_SECONDS)
            continue
        if item is None:
            continue
        ticket_id = _parse_ticket_id(item[1])
        if ticket_id is None:
            # Payload content is never logged.
            logger.warning("queue payload rejected reason=invalid_job_shape")
            continue
        try:
            await run_job(ctx, ticket_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "job wrapper failed class=%s ticket_id=%s", type(exc).__name__, ticket_id
            )


async def run_job(ctx: dict[str, Any], ticket_id: str) -> None:
    """One job with arq-like retry semantics: 3 attempts, backoff, then failed."""
    started = time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        job_ctx = dict(ctx, attempt=attempt)
        try:
            result = await pipeline.process_ticket(ticket_id, job_ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "pipeline raised class=%s ticket_id=%s", type(exc).__name__, ticket_id
            )
            return
        if not result.retryable:
            return
        if attempt >= MAX_ATTEMPTS:
            try:
                await pipeline.mark_failed(
                    ctx["pool"], ticket_id, result.error or "engine unavailable", attempt
                )
            except Exception as exc:
                logger.error(
                    "failure not recorded class=%s ticket_id=%s",
                    type(exc).__name__,
                    ticket_id,
                )
            logger.error(
                "job abandoned attempts=%d error_class=%s ticket_id=%s duration_ms=%d",
                attempt,
                result.error_class or "unknown",
                ticket_id,
                _ms(started),
            )
            return
        delay = BACKOFF_BASE_SECONDS ** attempt
        logger.warning(
            "job retry attempt=%d/%d backoff_s=%.0f ticket_id=%s",
            attempt,
            MAX_ATTEMPTS,
            delay,
            ticket_id,
        )
        await asyncio.sleep(delay)


async def triage_job(ctx: dict[str, Any], ticket_id: str) -> None:
    """arq-native passthrough: same execution path as the list consumer."""
    await run_job(ctx, ticket_id)


class WorkerSettings:
    functions = [triage_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(
        os.getenv("REDIS_URL") or "redis://localhost:6379/0"
    )
    # Upper bound for the passthrough path (3 attempts of engine + LLM + backoff).
    job_timeout = 180
