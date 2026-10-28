"""A new customer message invalidates the old suggestion and asks for a fresh one.

Contract pinned down here:

* every customer message commits a fresh ``queued`` ``ai_runs`` row together
  with the message and pushes the ticket onto ``triage:queue`` again, so the
  worker rebuilds the decision/draft from the whole thread,
* a customer message on an ``answered`` ticket reopens it
  (``answered -> in_progress``, a legal move of STATUS_TRANSITIONS),
* an agent message changes neither the status nor the queue,
* ``closed`` stays terminal: a customer reply there is a 409 and no job is
  queued.

The worker itself does not run in this suite (no Postgres/Redis/LLM here);
these tests pin the API contract the worker consumes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import AiRun

from conftest import (
    AGENT_EMAIL,
    AGENT_PASSWORD,
    add_suggestion,
    create_ticket,
    db_fetch,
    login,
    logout,
    queue_stub,
    register,
)


async def _runs_of(session, ticket_id: str) -> list[AiRun]:
    result = await session.scalars(
        select(AiRun).where(AiRun.ticket_id == uuid.UUID(str(ticket_id)))
    )
    return list(result)


def _queued_runs(ticket_id: str) -> list[AiRun]:
    # The worker never runs here, so every queued row stays queued.
    return [run for run in db_fetch(_runs_of, ticket_id) if run.status == "queued"]


def test_customer_reply_retriages_and_reopens(client):
    customer = register(client, display_name="Customer")
    login(client, customer["email"], customer["password"])
    ticket = create_ticket(client, subject="Follow-up", body="پیام اول مشتری")
    ticket_id = ticket["id"]
    assert ticket["status"] == "open"

    # Creation queued exactly one run and one push.
    assert len(_queued_runs(ticket_id)) == 1
    assert len(queue_stub.pushed) == 1

    # A suggestion already exists (the worker produced it before the follow-up).
    add_suggestion(ticket_id, draft_text="پاسخ اولیه", draft_status="ready")

    # --- follow-up while open: retriage, status untouched -------------------
    response = client.post(
        f"/api/tickets/{ticket_id}/messages", json={"body": "مشتری پیام جدید داد"}
    )
    assert response.status_code == 201, response.text
    assert client.get(f"/api/tickets/{ticket_id}").json()["status"] == "open"
    assert len(_queued_runs(ticket_id)) == 2
    assert len(queue_stub.pushed) == 2
    assert queue_stub.pushed[-1][0] == "triage:queue"
    assert ticket_id in queue_stub.pushed[-1][1]
    logout(client)

    # --- the agent answers: answered, and nothing new is queued -------------
    login(client, AGENT_EMAIL, AGENT_PASSWORD)
    response = client.post(
        f"/api/suggestions/{ticket_id}/decision", json={"decision": "accepted"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "answered"
    assert len(_queued_runs(ticket_id)) == 2  # a decision never queues work
    assert len(queue_stub.pushed) == 2

    # --- an agent message: no retriage, status untouched --------------------
    response = client.post(
        f"/api/tickets/{ticket_id}/messages", json={"body": "یادداشت پاسخ ایجنت"}
    )
    assert response.status_code == 201, response.text
    assert client.get(f"/api/tickets/{ticket_id}").json()["status"] == "answered"
    assert len(_queued_runs(ticket_id)) == 2
    assert len(queue_stub.pushed) == 2
    logout(client)

    # --- the customer replies again: reopen + retriage ----------------------
    login(client, customer["email"], customer["password"])
    response = client.post(
        f"/api/tickets/{ticket_id}/messages", json={"body": "پیام دوم مشتری"}
    )
    assert response.status_code == 201, response.text
    assert client.get(f"/api/tickets/{ticket_id}").json()["status"] == "in_progress"
    assert len(_queued_runs(ticket_id)) == 3
    assert len(queue_stub.pushed) == 3

    # The reopened ticket accepts a decision again (in_progress -> answered).
    logout(client)
    login(client, AGENT_EMAIL, AGENT_PASSWORD)
    response = client.post(
        f"/api/suggestions/{ticket_id}/decision",
        json={"decision": "edited", "text": "پاسخ تازه به پیام جدید"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "answered"


def test_agent_can_reopen_an_answered_ticket(client):
    customer = register(client)
    login(client, customer["email"], customer["password"])
    ticket = create_ticket(client)
    logout(client)

    login(client, AGENT_EMAIL, AGENT_PASSWORD)
    add_suggestion(ticket["id"], draft_text="پاسخ", draft_status="ready")
    response = client.post(
        f"/api/suggestions/{ticket['id']}/decision", json={"decision": "accepted"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "answered"

    # answered -> in_progress is a legal move for the agent too...
    response = client.patch(
        f"/api/tickets/{ticket['id']}/status", json={"status": "in_progress"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "in_progress"

    # ...but a manual reopen is not a customer message: nothing is queued.
    assert len(queue_stub.pushed) == 1  # only the creation push


def test_closed_ticket_refuses_customer_replies_and_queues_nothing(client):
    customer = register(client)
    login(client, customer["email"], customer["password"])
    ticket = create_ticket(client, subject="Closed", body="تیکت برای بستن")
    ticket_id = ticket["id"]
    logout(client)

    login(client, AGENT_EMAIL, AGENT_PASSWORD)
    assert client.patch(
        f"/api/tickets/{ticket_id}/status", json={"status": "in_progress"}
    ).status_code == 200
    assert client.patch(
        f"/api/tickets/{ticket_id}/status", json={"status": "closed"}
    ).status_code == 200
    logout(client)

    login(client, customer["email"], customer["password"])
    response = client.post(
        f"/api/tickets/{ticket_id}/messages", json={"body": "بعد از بستن شدن"}
    )
    assert response.status_code == 409, response.text
    assert client.get(f"/api/tickets/{ticket_id}").json()["status"] == "closed"
    # Closed is terminal: the refusal leaves the queue untouched.
    assert len(_queued_runs(ticket_id)) == 1
    assert len(queue_stub.pushed) == 1
