"""The ticket status state machine: moves only along the documented rules.

The schema allows ``open | in_progress | answered | closed``; this suite pins
down which moves actually exist:

* ``open -> in_progress`` and ``in_progress|answered -> closed`` via
  ``PATCH /api/tickets/{id}/status`` (agent only),
* ``open|in_progress -> answered`` only via accept/edit of a suggestion,
* every other move is a 409 — including anything out of ``closed``,
* a customer can never change a status.
"""

from __future__ import annotations

from conftest import (
    AGENT_EMAIL,
    AGENT_PASSWORD,
    add_suggestion,
    create_ticket,
    login,
    logout,
    register,
)


def _patch_status(client, ticket_id: str, status: str):
    return client.patch(f"/api/tickets/{ticket_id}/status", json={"status": status})


def test_ticket_status_transitions(client):
    customer = register(client, display_name="Customer")
    login(client, customer["email"], customer["password"])
    flowing = create_ticket(client, subject="flow", body="مسیر کامل وضعیت")
    closing = create_ticket(client, subject="close", body="بستن از در حال بررسی")
    from_answered = create_ticket(client, subject="answered close", body="بستن از پاسخ‌داده")
    closed = create_ticket(client, subject="terminal", body="تیکت بسته")
    logout(client)

    # --- customers can never move a status ---------------------------------
    login(client, customer["email"], customer["password"])
    response = _patch_status(client, flowing["id"], "in_progress")
    assert response.status_code == 403, response.text
    logout(client)

    login(client, AGENT_EMAIL, AGENT_PASSWORD)

    # --- open -> in_progress is legal --------------------------------------
    response = _patch_status(client, flowing["id"], "in_progress")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "in_progress"

    # --- open -> closed is refused: review comes first ---------------------
    response = _patch_status(client, closing["id"], "closed")
    assert response.status_code == 409, response.text
    assert client.get(f"/api/tickets/{closing['id']}").json()["status"] == "open"

    # --- in_progress -> closed is legal ------------------------------------
    response = _patch_status(client, closing["id"], "in_progress")
    assert response.status_code == 200, response.text
    response = _patch_status(client, closing["id"], "closed")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"

    # --- in_progress -> answered only via accept/edit ----------------------
    add_suggestion(flowing["id"], draft_text="پاسخ پیشنهادی", draft_status="ready")
    response = client.post(
        f"/api/suggestions/{flowing['id']}/decision", json={"decision": "accepted"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "answered"

    # The PATCH endpoint has no `answered` target at all.
    response = _patch_status(client, flowing["id"], "answered")
    assert response.status_code == 422, response.text

    # --- answered -> closed is legal ---------------------------------------
    response = _patch_status(client, from_answered["id"], "in_progress")
    assert response.status_code == 200, response.text
    add_suggestion(from_answered["id"], draft_text="پیشنهاد", draft_status="ready")
    response = client.post(
        f"/api/suggestions/{from_answered['id']}/decision", json={"decision": "accepted"}
    )
    assert response.status_code == 200, response.text
    response = _patch_status(client, from_answered["id"], "closed")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"

    # --- deciding on an answered/closed ticket is refused ------------------
    response = client.post(
        f"/api/suggestions/{from_answered['id']}/decision", json={"decision": "accepted"}
    )
    assert response.status_code == 409, response.text

    # --- closed is terminal -------------------------------------------------
    # Move `closed` into the closed state via the legal path, then try to
    # leave it (or re-close it): every move is refused.
    response = _patch_status(client, closed["id"], "in_progress")
    assert response.status_code == 200, response.text
    response = _patch_status(client, closed["id"], "closed")
    assert response.status_code == 200, response.text
    for target in ("in_progress", "closed"):
        response = _patch_status(client, closed["id"], target)
        assert response.status_code == 409, response.text
        assert "closed" in response.json()["detail"]
    assert client.get(f"/api/tickets/{closed['id']}").json()["status"] == "closed"

    # --- the customer can still see the final statuses ----------------------
    logout(client)
    login(client, customer["email"], customer["password"])
    statuses = {
        ticket["id"]: ticket["status"] for ticket in client.get("/api/tickets/my").json()
    }
    assert statuses[flowing["id"]] == "answered"
    assert statuses[closing["id"]] == "closed"
    assert statuses[from_answered["id"]] == "closed"
    assert statuses[closed["id"]] == "closed"
