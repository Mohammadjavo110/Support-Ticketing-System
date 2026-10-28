"""Pydantic request/response models for the public API surface."""

import re
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL_RE.match(value):
        raise ValueError("invalid email address")
    return value


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal["customer", "agent"] = "customer"

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _normalize_email(value)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _normalize_email(value)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: str


class TicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    author_id: uuid.UUID
    direction: str
    body: str
    created_at: datetime


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    subject: str
    body: str
    status: str
    priority: int
    category: str
    needs_manual_triage: bool
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut] = Field(default_factory=list)


class SuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticket_id: uuid.UUID
    category: str
    category_confidence: float
    priority: int
    priority_confidence: float
    needs_manual_triage: bool
    draft_text: Optional[str] = None
    draft_status: str
    model_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class InboxItem(BaseModel):
    id: uuid.UUID
    subject: str
    status: str
    priority: int
    category: str
    needs_manual_triage: bool
    created_at: datetime
    waiting_hours: float
    # Who the ticket belongs to — the agent inbox shows it; the UI falls back
    # to "نامعلوم" when it is missing.
    customer_name: Optional[str] = None
    suggestion: Optional[SuggestionOut] = None


class StatusPatchIn(BaseModel):
    # `answered` is deliberately absent: it is written only by the decision
    # endpoint, which creates the outbound message together with the status.
    # This endpoint handles the agent-driven moves (start review, close).
    status: Literal["in_progress", "closed"]


class DecisionIn(BaseModel):
    decision: Literal["accepted", "edited", "rejected"]
    text: Optional[str] = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _edited_needs_text(self) -> "DecisionIn":
        if self.decision == "edited" and not (self.text and self.text.strip()):
            raise ValueError("text must be a non-empty string when decision is 'edited'")
        return self


class DecisionOut(BaseModel):
    ticket_id: uuid.UUID
    decision: str
    status: str
    message_id: Optional[uuid.UUID] = None
