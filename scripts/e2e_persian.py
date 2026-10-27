#!/usr/bin/env python3
"""One real Persian ticket, end to end, against a running stack (stdlib only).

    $env:SMOKE_API='http://localhost:8010'; python scripts/e2e_persian.py

Walks a genuine Persian ticket through: customer login -> ticket ->
worker -> laya-serve -> hosted provider (stage 1) -> LLM draft (stage 2)
-> agent inbox -> human decision. Never assumes a draft exists.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = os.environ.get("SMOKE_API", "http://localhost:8010")
CUSTOMER = ("customer@example.com", "customer123")
AGENT = ("agent@example.com", "agent123")
TIMEOUT = 90.0

SUBJECT = "کسر دوباره مبلغ از کارت"
BODY = (
    "سلام، دیروز برای خرید اشتراک ۹۹ هزار تومان پرداخت کردم ولی مبلغ دو بار "
    "از حسابم برداشت شده. لطفاً هرچه زودتر پیگیری کنید چون موضوع فوری است."
)


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
    customer = Client()
    status, user = customer.call(
        "POST", "/api/auth/login", {"email": CUSTOMER[0], "password": CUSTOMER[1]}
    )
    print(f"[{'ok  ' if status == 200 else 'FAIL'}] customer login ({status})")
    if status != 200:
        return 1

    status, ticket = customer.call(
        "POST", "/api/tickets", {"subject": SUBJECT, "body": BODY}
    )
    ticket_id = (ticket or {}).get("id")
    print(f"[{'ok  ' if status == 201 else 'FAIL'}] Persian ticket created ({status}) id={ticket_id}")
    if status != 201:
        return 1

    suggestion = None
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        status, suggestion = customer.call("GET", f"/api/suggestions/{ticket_id}")
        if status == 200 and suggestion and suggestion.get("draft_status") not in (
            None,
            "pending",
        ):
            break
        time.sleep(1.0)
    if not suggestion:
        print("[FAIL] no suggestion row appeared (worker never answered)")
        return 1

    print("--- stage 1 (typed decision) ---")
    print(f"  category            = {suggestion.get('category')}")
    print(f"  category_confidence = {suggestion.get('category_confidence')}")
    print(f"  priority            = {suggestion.get('priority')}")
    print(f"  priority_confidence = {suggestion.get('priority_confidence')}")
    print(f"  needs_manual_triage = {suggestion.get('needs_manual_triage')}")
    print(f"  model_name          = {suggestion.get('model_name')}")
    print(f"  draft_status        = {suggestion.get('draft_status')}")
    draft = suggestion.get("draft_text") or ""
    print("--- stage 2 (LLM draft) ---")
    print(f"  draft_text ({len(draft)} chars): {draft[:300]}")

    agent = Client()
    status, _ = agent.call(
        "POST", "/api/auth/login", {"email": AGENT[0], "password": AGENT[1]}
    )
    print(f"[{'ok  ' if status == 200 else 'FAIL'}] agent login ({status})")

    status, inbox = agent.call("GET", "/api/inbox")
    ids = [row["id"] for row in (inbox or [])]
    in_inbox = status == 200 and ticket_id in ids
    print(f"[{'ok  ' if in_inbox else 'FAIL'}] ticket in agent inbox (rows={len(ids or [])})")

    print("--- stage 3 (human decision) ---")
    if suggestion.get("draft_status") == "ready":
        status, result = agent.call(
            "POST", f"/api/suggestions/{ticket_id}/decision", {"decision": "accepted"}
        )
        ok = status == 200 and (result or {}).get("status") == "answered"
        print(f"[{'ok  ' if ok else 'FAIL'}] accept ready draft -> answered ({status})")
        status, thread = customer.call("GET", f"/api/tickets/{ticket_id}")
        messages = (thread or {}).get("messages") or []
        print(f"       thread now has {len(messages)} message(s); "
              f"status={(thread or {}).get('status')}")
        return 0 if ok else 1

    status, result = agent.call(
        "POST", f"/api/suggestions/{ticket_id}/decision", {"decision": "accepted"}
    )
    refused = status == 409
    print(f"[{'ok  ' if refused else 'FAIL'}] accept without a draft is refused ({status})")
    return 0 if refused else 1


if __name__ == "__main__":
    sys.exit(main())
