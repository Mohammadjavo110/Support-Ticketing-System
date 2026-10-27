"""Creating a ticket must succeed while the engine is completely down.

The engine is a separate container reached through a queue: nothing it does may
be on the synchronous path of ``POST /api/tickets``.  With no engine listening
at all, the ticket must still be stored, queued exactly once and readable —
and no suggestion may be invented on its behalf.
"""

from __future__ import annotations

import json
import socket
import uuid
from urllib.parse import urlparse

from sqlalchemy import select

from app.models import AiRun, AiSuggestion

from conftest import (
    ENGINE_URL,
    create_ticket,
    db_fetch,
    login,
    queue_stub,
    register,
)


def _engine_is_down() -> bool:
    """True when nothing accepts connections on the configured engine URL."""
    parsed = urlparse(ENGINE_URL)
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=0.5):
            return False
    except OSError:
        return True


async def _runs_of(session, ticket_id: str) -> list[AiRun]:
    result = await session.scalars(
        select(AiRun).where(AiRun.ticket_id == uuid.UUID(str(ticket_id)))
    )
    return list(result)


async def _suggestions_of(session, ticket_id: str) -> list[AiSuggestion]:
    result = await session.scalars(
        select(AiSuggestion).where(
            AiSuggestion.ticket_id == uuid.UUID(str(ticket_id))
        )
    )
    return list(result)


def test_ticket_created_when_engine_down(client):
    assert _engine_is_down(), "the engine URL must be unreachable for this test"

    user = register(client, display_name="No engine")
    login(client, user["email"], user["password"])

    ticket = create_ticket(
        client, subject="Engine is down", body="تیکت بدون موتور طبقه‌بندی"
    )

    # 1. The ticket exists with its defaults; the engine was never consulted.
    assert ticket["status"] == "open"
    assert ticket["category"] == "unknown"
    assert ticket["priority"] == 2
    assert ticket["needs_manual_triage"] is False

    # 2. The job was pushed to the queue exactly once, with the right payload.
    assert len(queue_stub.pushed) == 1
    key, payload = queue_stub.pushed[0]
    assert key == "triage:queue"
    assert json.loads(payload)["ticket_id"] == ticket["id"]

    # 3. The audit trail says queued; nothing fabricated a suggestion.
    runs = db_fetch(_runs_of, ticket["id"])
    assert [run.status for run in runs] == ["queued"]
    assert db_fetch(_suggestions_of, ticket["id"]) == []

    # 4. The customer can read the ticket straight back.
    mine = client.get("/api/tickets/my")
    assert mine.status_code == 200
    assert ticket["id"] in {row["id"] for row in mine.json()}
