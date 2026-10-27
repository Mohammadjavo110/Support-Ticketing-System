"""Request/response contract for POST /predict.

Worker payload:
    {"text": "...", "questions": [{"id", "type", ...}, ...], "model": "..."}
Violations (text out of 1..5000, questions not a list, duplicate ids,
malformed question shapes) surface as HTTP 422 from FastAPI's validator.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SUPPORTED_TYPES = ("choice", "score", "noul")


class Question(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    type: str
    options: list[str] | None = None
    min: int | None = None
    max: int | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "Question":
        if self.type not in SUPPORTED_TYPES:
            raise ValueError(f"unsupported question type: {self.type!r}")
        if self.type == "choice":
            if not self.options or not all(isinstance(o, str) and o for o in self.options):
                raise ValueError(f"question {self.id!r}: choice requires a non-empty options list")
        if self.type == "score":
            if self.min is None or self.max is None:
                raise ValueError(f"question {self.id!r}: score requires min and max")
            if self.min > self.max:
                raise ValueError(f"question {self.id!r}: min must be <= max")
        return self


class PredictRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    questions: list[Question]
    model: str | None = None

    @model_validator(mode="after")
    def _unique_ids(self) -> "PredictRequest":
        seen: set[str] = set()
        dupes: set[str] = set()
        for q in self.questions:
            if q.id in seen:
                dupes.add(q.id)
            seen.add(q.id)
        if dupes:
            raise ValueError(f"duplicate question ids: {', '.join(sorted(dupes))}")
        return self


class Answer(BaseModel):
    value: Any
    confidence: float


class PredictResponse(BaseModel):
    answers: dict[str, Answer]
    model: str
    mode: Literal["hosted", "local"]
    latency_ms: int
