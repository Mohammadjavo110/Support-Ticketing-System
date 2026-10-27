"""FastAPI application: auth, tickets, inbox and suggestion decisions.

Writer ownership: this service owns users, sessions, tickets and messages, plus
the initial ai_runs row created on ticket creation. It never writes
ai_suggestions and only changes ticket.status inside the decision endpoint.
"""

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.config import settings
from app.db import SessionLocal, get_session, init_db
from app.models import AiRun, AiSuggestion, Message, SuggestionEvent, Ticket, User
from app.security import (
    clear_session,
    create_session,
    current_user,
    hash_password,
    require_agent,
    verify_password,
)

log = logging.getLogger("app.api")

TRIAGE_QUEUE_KEY = "triage:queue"

redis_client: Redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _seed_demo_users()
    log.info("api started")
    yield
    await redis_client.aclose()


app = FastAPI(title="Support Ticket API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _seed_demo_users() -> None:
    """Create the demo agent/customer so the UI works on a fresh database."""
    demos = (
        ("agent@example.com", "agent123", "Demo Agent", "agent"),
        ("customer@example.com", "customer123", "Demo Customer", "customer"),
    )
    async with SessionLocal() as db:
        for email, password, display_name, role in demos:
            taken = await db.scalar(select(User.id).where(User.email == email))
            if taken is None:
                db.add(
                    User(
                        email=email,
                        password_hash=hash_password(password),
                        display_name=display_name,
                        role=role,
                    )
                )
        try:
            await db.commit()
        except IntegrityError:
            # Concurrent replica seeded the same rows; nothing to do.
            await db.rollback()
            log.info("demo users already present")


async def _visible_ticket(ticket_id: uuid.UUID, user: User, db: AsyncSession) -> Ticket:
    """Load a ticket scoped to the caller; agents see everything."""
    stmt = select(Ticket).where(Ticket.id == ticket_id)
    if user.role != "agent":
        stmt = stmt.where(Ticket.customer_id == user.id)
    ticket = await db.scalar(stmt)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


def _waiting_hours(created_at: datetime) -> float:
    # Postgres returns aware timestamps; be tolerant of naive values.
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - created_at).total_seconds() / 3600.0, 2)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.post("/api/auth/register", status_code=201, response_model=schemas.UserOut)
async def register(
    payload: schemas.RegisterIn, db: AsyncSession = Depends(get_session)
) -> schemas.UserOut:
    taken = await db.scalar(select(User.id).where(User.email == payload.email))
    if taken is not None:
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        role=payload.role,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered")
    await db.refresh(user)
    log.info("user registered id=%s role=%s", user.id, user.role)
    return schemas.UserOut.model_validate(user)


@app.post("/api/auth/login", response_model=schemas.UserOut)
async def login(
    payload: schemas.LoginIn,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> schemas.UserOut:
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        log.warning("login failed")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await create_session(db, user, response)
    log.info("login ok id=%s role=%s", user.id, user.role)
    return schemas.UserOut.model_validate(user)


@app.post("/api/auth/logout")
async def logout(
    request: Request, response: Response, db: AsyncSession = Depends(get_session)
) -> dict:
    await clear_session(db, request, response)
    log.info("logout")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


@app.post("/api/tickets", status_code=201, response_model=schemas.TicketOut)
async def create_ticket(
    payload: schemas.TicketCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> schemas.TicketOut:
    started = time.perf_counter()
    ticket = Ticket(
        customer_id=user.id,
        subject=payload.subject,
        body=payload.body,
        status="open",
    )
    db.add(ticket)
    await db.flush()  # obtain ticket.id for the run row
    db.add(AiRun(ticket_id=ticket.id, status="queued"))
    await db.commit()  # ticket + ai_runs land together, before any enqueue
    await db.refresh(ticket)

    try:
        await redis_client.lpush(
            TRIAGE_QUEUE_KEY, json.dumps({"ticket_id": str(ticket.id)})
        )
    except Exception:
        # Database is the source of truth; the queued ai_runs row records the job.
        log.error("enqueue failed ticket_id=%s", ticket.id, exc_info=True)

    log.info(
        "ticket created id=%s status=%s duration_ms=%d",
        ticket.id,
        ticket.status,
        int((time.perf_counter() - started) * 1000),
    )
    return schemas.TicketOut.model_validate(ticket)


@app.get("/api/tickets/my", response_model=list[schemas.TicketOut])
async def my_tickets(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[schemas.TicketOut]:
    rows = (
        await db.scalars(
            select(Ticket)
            .where(Ticket.customer_id == user.id)
            .order_by(Ticket.created_at.desc())
        )
    ).all()
    return [schemas.TicketOut.model_validate(row) for row in rows]


@app.get("/api/tickets/{ticket_id}", response_model=schemas.TicketOut)
async def get_ticket(
    ticket_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> schemas.TicketOut:
    ticket = await _visible_ticket(ticket_id, user, db)
    messages = (
        await db.scalars(
            select(Message)
            .where(Message.ticket_id == ticket.id)
            .order_by(Message.created_at.asc())
        )
    ).all()
    out = schemas.TicketOut.model_validate(ticket)
    out.messages = [schemas.MessageOut.model_validate(m) for m in messages]
    return out


@app.post("/api/tickets/{ticket_id}/messages", status_code=201, response_model=schemas.MessageOut)
async def add_message(
    ticket_id: uuid.UUID,
    payload: schemas.MessageIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> schemas.MessageOut:
    ticket = await _visible_ticket(ticket_id, user, db)
    if ticket.status == "closed" and user.role != "agent":
        raise HTTPException(
            status_code=409, detail="Ticket is closed; create a new ticket instead"
        )
    direction = "out" if user.role == "agent" else "in"
    message = Message(
        ticket_id=ticket.id,
        author_id=user.id,
        direction=direction,
        body=payload.body,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    log.info("message added ticket_id=%s direction=%s", ticket.id, direction)
    return schemas.MessageOut.model_validate(message)


# ---------------------------------------------------------------------------
# Agent workbench
# ---------------------------------------------------------------------------


@app.get("/api/inbox", response_model=list[schemas.InboxItem])
async def inbox(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> list[schemas.InboxItem]:
    require_agent(user)
    tickets = (
        await db.scalars(
            select(Ticket).order_by(
                Ticket.needs_manual_triage.desc(),
                Ticket.priority.asc(),
                Ticket.created_at.asc(),
            )
        )
    ).all()

    suggestions: dict = {}
    if tickets:
        rows = await db.scalars(
            select(AiSuggestion).where(
                AiSuggestion.ticket_id.in_([t.id for t in tickets])
            )
        )
        suggestions = {row.ticket_id: row for row in rows}

    items = []
    for ticket in tickets:
        suggestion = suggestions.get(ticket.id)
        items.append(
            schemas.InboxItem(
                id=ticket.id,
                subject=ticket.subject,
                status=ticket.status,
                priority=ticket.priority,
                category=ticket.category,
                needs_manual_triage=ticket.needs_manual_triage,
                created_at=ticket.created_at,
                waiting_hours=_waiting_hours(ticket.created_at),
                suggestion=(
                    schemas.SuggestionOut.model_validate(suggestion)
                    if suggestion is not None
                    else None
                ),
            )
        )
    log.info("inbox served count=%d", len(items))
    return items


@app.get("/api/suggestions/{ticket_id}", response_model=schemas.SuggestionOut)
async def get_suggestion(
    ticket_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> schemas.SuggestionOut:
    await _visible_ticket(ticket_id, user, db)
    suggestion = await db.get(AiSuggestion, ticket_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return schemas.SuggestionOut.model_validate(suggestion)


@app.post("/api/suggestions/{ticket_id}/decision", response_model=schemas.DecisionOut)
async def decide(
    ticket_id: uuid.UUID,
    payload: schemas.DecisionIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> schemas.DecisionOut:
    require_agent(user)
    ticket = await db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    suggestion = await db.get(AiSuggestion, ticket_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    message: Message | None = None
    if payload.decision in ("accepted", "edited"):
        if payload.decision == "accepted" and (
            suggestion.draft_status == "failed" or not suggestion.draft_text
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Suggestion has no draft to accept "
                    f"(draft_status={suggestion.draft_status})"
                ),
            )
        body = (
            suggestion.draft_text
            if payload.decision == "accepted"
            else payload.text  # validated non-empty by DecisionIn
        )
        message = Message(
            ticket_id=ticket.id, author_id=user.id, direction="out", body=body
        )
        db.add(message)
        ticket.status = "answered"

    db.add(
        SuggestionEvent(
            ticket_id=ticket.id,
            decision=payload.decision,
            edited=payload.decision == "edited",
        )
    )
    await db.commit()

    log.info(
        "decision ticket_id=%s decision=%s status=%s",
        ticket.id,
        payload.decision,
        ticket.status,
    )
    return schemas.DecisionOut(
        ticket_id=ticket.id,
        decision=payload.decision,
        status=ticket.status,
        message_id=message.id if message is not None else None,
    )
