"""Classifier contract and the Decision model (PLAN §5)."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from t.classify.schema import StructureSchema


class Decision(BaseModel):
    board_id: str  # only an existing board from the schema
    list_id: str  # only a list of that board
    label_ids: list[str] = []  # only existing labels
    title: str  # short, cleaned of the date phrase
    description: str | None = None
    due_phrase: str | None = None  # raw date text, NOT a date
    confidence: float = Field(ge=0.0, le=1.0)  # 0..1


class Classifier(Protocol):
    def decide(self, text: str, schema: StructureSchema) -> Decision: ...
