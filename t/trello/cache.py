"""Local cache of the Trello structure (boards, lists, labels).

Stored as JSON with a ``fetched_at`` timestamp; entries older than the
configured TTL are refetched by ``t sync`` / ``t add``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from t.trello.models import Board, Label, TrelloList


class BoardStructure(BaseModel):
    board: Board
    lists: list[TrelloList] = []
    labels: list[Label] = []


class CachedStructure(BaseModel):
    fetched_at: datetime
    boards: list[BoardStructure] = []


def utcnow() -> datetime:
    return datetime.now(UTC)


def save_cache(structure: CachedStructure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(structure.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_cache(path: Path) -> CachedStructure | None:
    if not path.exists():
        return None
    try:
        return CachedStructure.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_fresh(cached: CachedStructure, ttl_seconds: int) -> bool:
    age = (utcnow() - cached.fetched_at).total_seconds()
    return age < ttl_seconds
