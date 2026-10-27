"""Two customers must never see, read or write each other's tickets.

Covers the isolation rules of the brief: scoping by owner, 404 instead of an
existence leak, and the agent as the only role allowed to see everything.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import Ticket

from conftest import (
    AGENT_EMAIL,
    AGENT_PASSWORD,
    create_ticket,
    db_fetch,
    login,
    logout,
    register,
)


def _my_ticket_ids(client) -> list[str]:
    response = client.get("/api/tickets/my")
    assert response.status_code == 200, response.text
    return [ticket["id"] for ticket in response.json()]


async def _owner_of(session, ticket_id: str) -> str:
    ticket = await session.get(Ticket, uuid.UUID(str(ticket_id)))
    assert ticket is not None
    return str(ticket.customer_id)


def test_two_customers_are_isolated(client):
    alice = register(client, display_name="Alice")
    bob = register(client, display_name="Bob")

    # Alice opens a ticket.
    login(client, alice["email"], alice["password"])
    alice_ticket = create_ticket(client, subject="Alice subject", body="Alice body")
    logout(client)

    # Bob opens his own ticket.
    login(client, bob["email"], bob["password"])
    bob_ticket = create_ticket(client, subject="Bob subject", body="Bob body")

    bob_list = _my_ticket_ids(client)
    assert bob_ticket["id"] in bob_list
    assert alice_ticket["id"] not in bob_list

    # Bob cannot read, write or act on Alice's ticket.
    assert client.get(f"/api/tickets/{alice_ticket['id']}").status_code == 404
    assert (
        client.post(
            f"/api/tickets/{alice_ticket['id']}/messages", json={"body": "hi"}
        ).status_code
        == 404
    )
    assert client.get(f"/api/suggestions/{alice_ticket['id']}").status_code == 404
    # A decision requires the agent role, which is checked before the lookup.
    assert (
        client.post(
            f"/api/suggestions/{alice_ticket['id']}/decision",
            json={"decision": "rejected"},
        ).status_code
        == 403
    )
    logout(client)

    # The same checks from Alice's side.
    login(client, alice["email"], alice["password"])
    alice_list = _my_ticket_ids(client)
    assert alice_ticket["id"] in alice_list
    assert bob_ticket["id"] not in alice_list
    assert client.get(f"/api/tickets/{bob_ticket['id']}").status_code == 404
    assert client.get(f"/api/suggestions/{bob_ticket['id']}").status_code == 404
    logout(client)

    # Ownership is stored correctly, not only filtered in the API layer.
    assert db_fetch(_owner_of, alice_ticket["id"]) == alice["id"]
    assert db_fetch(_owner_of, bob_ticket["id"]) == bob["id"]

    # The agent role sees both, and Bob's rejected write left no trace.
    login(client, AGENT_EMAIL, AGENT_PASSWORD)
    assert client.get(f"/api/tickets/{alice_ticket['id']}").status_code == 200
    assert client.get(f"/api/tickets/{bob_ticket['id']}").status_code == 200
    assert client.get(f"/api/tickets/{alice_ticket['id']}").json()["messages"] == []

    inbox_ids = {item["id"] for item in client.get("/api/inbox").json()}
    assert alice_ticket["id"] in inbox_ids
    assert bob_ticket["id"] in inbox_ids
