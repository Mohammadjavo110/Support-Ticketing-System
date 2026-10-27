#!/usr/bin/env python3
"""End-to-end smoke test against a running stack (stdlib only).

Run after ``docker compose up --build -d``:

    python scripts/smoke.py

It walks one ticket through the whole path: health, customer login, ticket
creation, the worker's decision, the agent inbox and a human decision on the
suggestion. It never assumes a draft exists — the LLM is optional, so a
``failed`` draft is an accepted outcome (see README section 8).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

API = __import__("os").environ.get("SMOKE_API", "http://localhost:8000")
CUSTOMER = ("customer@example.com", "customer123")
AGENT = ("agent@example.com", "agent123")
TIMEOUT = 60.0

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "ok  " if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"[{mark}] {label}{suffix}")
    if not ok:
        failures.append(label)


class Client:
    """Minimal cookie-aware JSON client."""

    def __init__(self) -> None:
        self.cookie = ""

    def call(self, method: str, path: str, payload: dict | None = None):
        request = urllib.request.Request(API + path, method=method)
        request.add_header("Content-Type", "application/json")
        if self.cookie:
            request.add_header("Cookie", self.cookie)
        data = json.dumps(payload).encode() if payload is not None else None
        try:
            with urllib.request.urlopen(request, data=data, timeout=15) as response:
                body = response.read().decode()
                raw = response.headers.get("Set-Cookie", "")
                if raw:
                    self.cookie = raw.split(";", 1)[0]
                return response.status, json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            body = error.read().decode()
            try:
                parsed = json.loads(body) if body else None
            except ValueError:
                parsed = {"raw": body}
            return error.code, parsed


def main() -> int:
    # 1. Health ----------------------------------------------------------------
    status, health = Client().call("GET", "/healthz")
    check("api healthz answers", status == 200 and health.get("status") == "ok", str(health))

    # 2. Customer logs in and opens a ticket -----------------------------------
    customer = Client()
    status, user = customer.call("POST", "/api/auth/login", {"email": CUSTOMER[0], "password": CUSTOMER[1]})
    check("customer login", status == 200, str(user))
    if status != 200:
        return report()

    status, ticket = customer.call(
        "POST",
        "/api/tickets",
        {"subject": "smoke: card was charged twice", "body": "Two charges on my card, please refund."},
    )
    check("ticket created", status == 201 and ticket.get("status") == "open", str(status))
    if status != 201:
        return report()
    ticket_id = ticket["id"]

    # 3. The worker picks it up and produces a decision ------------------------
    suggestion = None
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        status, suggestion = customer.call("GET", f"/api/suggestions/{ticket_id}")
        if status == 200 and suggestion is not None:
            break
        time.sleep(1.0)
    check("worker produced a decision", status == 200, f"status={status}")
    if suggestion:
        check(
            "decision is typed, not a guess",
            suggestion.get("category") in {"billing", "technical", "shipping", "account", "other", "unknown"},
            f"category={suggestion.get('category')} confidence={suggestion.get('category_confidence')}",
        )
        check(
            "confidence recorded",
            isinstance(suggestion.get("category_confidence"), (int, float)),
        )
        print(
            f"       category={suggestion.get('category')} "
            f"priority={suggestion.get('priority')} "
            f"draft={suggestion.get('draft_status')}"
        )

    # 4. The agent sees it in the inbox ----------------------------------------
    agent = Client()
    status, _ = agent.call("POST", "/api/auth/login", {"email": AGENT[0], "password": AGENT[1]})
    check("agent login", status == 200)

    status, inbox = agent.call("GET", "/api/inbox")
    ids = [row["id"] for row in (inbox or [])]
    check("ticket is in the agent inbox", status == 200 and ticket_id in ids, f"rows={len(ids or [])}")

    # 5. A human decision on the suggestion -------------------------------------
    if suggestion and suggestion.get("draft_status") == "ready":
        status, result = agent.call(
            "POST", f"/api/suggestions/{ticket_id}/decision", {"decision": "accepted"}
        )
        check("accepting a ready draft answers the ticket", status == 200 and result.get("status") == "answered")
    else:
        # No draft: accept must be refused, reject must be recorded.
        status, result = agent.call(
            "POST", f"/api/suggestions/{ticket_id}/decision", {"decision": "accepted"}
        )
        check("accepting a missing draft is refused (409)", status == 409, f"status={status}")
        status, result = agent.call(
            "POST", f"/api/suggestions/{ticket_id}/decision", {"decision": "rejected"}
        )
        check("rejecting is recorded", status == 200, f"status={status}")

    status, thread = customer.call("GET", f"/api/tickets/{ticket_id}")
    check("customer can still read the ticket", status == 200, f"status={status}")

    return report()


def report() -> int:
    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
