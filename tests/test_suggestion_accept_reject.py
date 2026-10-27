"""The agent decision on a suggestion: accept, reject, edit — and the 409 case.

The suggestion is not a message: only accepting or editing one creates an
outbound message and moves the ticket to ``answered``.  Rejecting only records
the human decision.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import Message, SuggestionEvent

from conftest import (
    AGENT_EMAIL,
    AGENT_PASSWORD,
    add_suggestion,
    create_ticket,
    db_fetch,
    login,
    logout,
    register,
)


async def _messages_of(session, ticket_id: str) -> list[Message]:
    result = await session.scalars(
        select(Message)
        .where(Message.ticket_id == uuid.UUID(str(ticket_id)))
        .order_by(Message.created_at.asc())
    )
    return list(result)


async def _events_of(session, ticket_id: str) -> list[SuggestionEvent]:
    result = await session.scalars(
        select(SuggestionEvent).where(
            SuggestionEvent.ticket_id == uuid.UUID(str(ticket_id))
        )
    )
    return list(result)


def test_suggestion_accept_reject(client):
    customer = register(client, display_name="Customer")
    login(client, customer["email"], customer["password"])

    accepted = create_ticket(client, subject="accept me", body="بدون پاسخ هنوز")
    failed = create_ticket(client, subject="no draft", body="بدون پاسخ هنوز")
    rejected = create_ticket(client, subject="reject me", body="بدون پاسخ هنوز")
    edited = create_ticket(client, subject="edit me", body="بدون پاسخ هنوز")
    logout(client)

    # Rows the worker would have written; the worker is not running here.
    add_suggestion(accepted["id"], draft_text="پاسخ پیشنهادی موتور", draft_status="ready")
    add_suggestion(failed["id"], draft_text=None, draft_status="failed")
    add_suggestion(rejected["id"], draft_text="پیشنهاد قابل رد", draft_status="ready")
    add_suggestion(edited["id"], draft_text="پیشنهاد قابل ویرایش", draft_status="ready")

    login(client, AGENT_EMAIL, AGENT_PASSWORD)

    # --- accept: creates the outbound message and answers the ticket -------
    response = client.post(
        f"/api/suggestions/{accepted['id']}/decision", json={"decision": "accepted"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "accepted"
    assert response.json()["status"] == "answered"
    assert response.json()["message_id"]

    thread = client.get(f"/api/tickets/{accepted['id']}").json()
    assert thread["status"] == "answered"
    outbound = [m for m in thread["messages"] if m["direction"] == "out"]
    assert [m["body"] for m in outbound] == ["پاسخ پیشنهادی موتور"]
    assert [
        (event.decision, bool(event.edited)) for event in db_fetch(_events_of, accepted["id"])
    ] == [("accepted", False)]

    # --- accept with no draft: refused, nothing written --------------------
    conflict = client.post(
        f"/api/suggestions/{failed['id']}/decision", json={"decision": "accepted"}
    )
    assert conflict.status_code == 409, conflict.text

    thread = client.get(f"/api/tickets/{failed['id']}").json()
    assert thread["status"] == "open"
    assert thread["messages"] == []
    assert db_fetch(_events_of, failed["id"]) == []

    # --- reject: records the decision, writes no message, keeps status -----
    response = client.post(
        f"/api/suggestions/{rejected['id']}/decision", json={"decision": "rejected"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["message_id"] is None
    assert response.json()["status"] == "open"

    thread = client.get(f"/api/tickets/{rejected['id']}").json()
    assert thread["status"] == "open"
    assert thread["messages"] == []
    assert [
        (event.decision, bool(event.edited)) for event in db_fetch(_events_of, rejected["id"])
    ] == [("rejected", False)]
    assert db_fetch(_messages_of, rejected["id"]) == []

    # --- edit: the agent's own text becomes the outbound message -----------
    response = client.post(
        f"/api/suggestions/{edited['id']}/decision",
        json={"decision": "edited", "text": "متن ویرایش‌شده توسط کارشناس"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "answered"

    thread = client.get(f"/api/tickets/{edited['id']}").json()
    assert thread["status"] == "answered"
    outbound = [m for m in thread["messages"] if m["direction"] == "out"]
    assert [m["body"] for m in outbound] == ["متن ویرایش‌شده توسط کارشناس"]
    assert [
        (event.decision, bool(event.edited)) for event in db_fetch(_events_of, edited["id"])
    ] == [("edited", True)]

    # --- an edit without text is a validation error, not a silent no-op ----
    invalid = client.post(
        f"/api/suggestions/{rejected['id']}/decision",
        json={"decision": "edited", "text": "   "},
    )
    assert invalid.status_code == 422
