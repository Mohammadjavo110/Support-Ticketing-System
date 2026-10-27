"""Fixtures for running the API with **no external services**.

While the suite runs there is no PostgreSQL, no Redis, no engine container and
no LLM anywhere:

* ``DATABASE_URL`` points at a throwaway SQLite file and the schema is built
  from the ORM metadata (``schema.sql`` is PostgreSQL-only SQL),
* the queue client is replaced by an in-memory stub that records pushes,
* the engine URL points at a port nobody listens on.

The environment is set *before* the application is imported, because settings
are read exactly once at import time.  The engine/session factory is also
swapped before ``app.main`` is imported, so ``app.main`` binds the test
versions (it imports those names directly).
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sqlite3
import sys
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = pathlib.Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

# ---------------------------------------------------------------------------
# Environment (must precede every ``app.*`` import)
# ---------------------------------------------------------------------------


def _closed_port() -> int:
    """Bind an ephemeral port and release it, so nothing answers on it."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


DB_PATH = pathlib.Path(tempfile.mkdtemp(prefix="ticket-tests-")) / "test.db"
ENGINE_PORT = _closed_port()

os.environ.update(
    {
        "DATABASE_URL": f"sqlite+aiosqlite:///{DB_PATH.as_posix()}",
        "REDIS_URL": f"redis://127.0.0.1:{ENGINE_PORT}/0",
        "SECRET_KEY": "test-only-secret",
        "SESSION_TTL_HOURS": "72",
        "CORS_ORIGINS": "http://localhost:5173",
        "ENGINE_PROVIDER": "laya",
        "LAYA_API_URL": f"http://127.0.0.1:{ENGINE_PORT}/predict",
        "LAYA_API_KEY": "",
        "ENGINE_MIN_CONFIDENCE": "0.55",
        "ENGINE_TIMEOUT_SECONDS": "1",
    }
)

ENGINE_URL = os.environ["LAYA_API_URL"]

# Demo users seeded by the API on startup (see app/main.py).
AGENT_EMAIL = "agent@example.com"
AGENT_PASSWORD = "agent123"

# ---------------------------------------------------------------------------
# Application wiring
# ---------------------------------------------------------------------------

from app import db as app_db  # noqa: E402  (imported after the env is set)

# NullPool: every session opens its own connection and closes it again, so no
# connection is ever carried across two event loops (TestClient builds a fresh
# loop per test, and helpers below build yet another one).
app_db.engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
app_db.SessionLocal = async_sessionmaker(app_db.engine, expire_on_commit=False)

from app import main as app_main  # noqa: E402
from app import models  # noqa: E402,F401  (registers the tables on Base)
from app.models import AiSuggestion  # noqa: E402


async def _create_schema() -> None:
    async with app_db.engine.begin() as conn:
        await conn.run_sync(app_db.Base.metadata.create_all)


class QueueStub:
    """In-memory stand-in for the Redis list the API pushes jobs onto."""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []

    def reset(self) -> None:
        self.pushed.clear()

    async def lpush(self, key: str, value: str) -> int:
        self.pushed.append((key, value))
        return len(self.pushed)

    async def aclose(self) -> None:  # called by the lifespan on shutdown
        return None


queue_stub = QueueStub()

app_main.redis_client = queue_stub
app_main.init_db = _create_schema


def reset_database() -> None:
    """Empty every table so each test starts from a clean database."""
    conn = sqlite3.connect(DB_PATH, timeout=15)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in reversed(app_db.Base.metadata.sorted_tables):
            conn.execute(f'DELETE FROM "{table.name}"')
        conn.commit()
    except sqlite3.OperationalError:
        pass  # first run: the tables do not exist yet
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    reset_database()
    queue_stub.reset()
    with TestClient(app_main.app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Helpers shared by the test modules
# ---------------------------------------------------------------------------


def register(
    client: TestClient,
    *,
    display_name: str = "User",
    role: str = "customer",
    password: str = "pass1234",
) -> dict:
    """Register a user with a unique address and return its credentials."""
    email = f"{uuid.uuid4().hex[:10]}@example.com"
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": display_name,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return {"email": email, "password": password, "id": response.json()["id"]}


def login(client: TestClient, email: str, password: str) -> dict:
    response = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


def logout(client: TestClient) -> None:
    response = client.post("/api/auth/logout")
    assert response.status_code == 200, response.text


def create_ticket(client: TestClient, subject: str = "Subject", body: str = "Body") -> dict:
    response = client.post("/api/tickets", json={"subject": subject, "body": body})
    assert response.status_code == 201, response.text
    return response.json()


def db_fetch(fn, *args):
    """Run ``await fn(session, *args)`` on a fresh session of its own."""

    async def _run():
        async with app_db.SessionLocal() as session:
            return await fn(session, *args)

    return asyncio.run(_run())


def add_suggestion(
    ticket_id: str,
    *,
    draft_text: str | None = "پاسخ پیشنهادی موتور",
    draft_status: str = "ready",
    category: str = "technical",
    category_confidence: float = 0.91,
    priority: int = 2,
    priority_confidence: float = 0.8,
    needs_manual_triage: bool = False,
) -> None:
    """Insert the row the worker would have written (the worker is not running)."""

    async def _seed(session):
        session.add(
            AiSuggestion(
                ticket_id=uuid.UUID(str(ticket_id)),
                category=category,
                category_confidence=category_confidence,
                priority=priority,
                priority_confidence=priority_confidence,
                needs_manual_triage=needs_manual_triage,
                draft_text=draft_text,
                draft_status=draft_status,
                model_name="test-engine",
            )
        )
        await session.commit()

    async def _run():
        async with app_db.SessionLocal() as session:
            await _seed(session)

    asyncio.run(_run())
