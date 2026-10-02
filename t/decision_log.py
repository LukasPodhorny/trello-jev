"""JSONL log of classification decisions (for later accuracy evaluation)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from t.classify.base import Decision


class DecisionLogEntry(BaseModel):
    ts: datetime
    input_text: str
    decision: Decision
    card_id: str | None = None
    card_url: str | None = None
    # "parser": every entry written before the Jev date engine existed used it.
    date_engine: str = "parser"
    # "cli": every entry written before the MCP server existed came from it.
    source: str = "cli"


def append_log(entry: DecisionLogEntry, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(entry.model_dump_json() + "\n")
    return path


def read_last(path: Path, limit: int) -> list[DecisionLogEntry]:
    """Return up to ``limit`` newest entries; skips corrupt lines."""
    if not path.exists():
        return []
    entries: list[DecisionLogEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(DecisionLogEntry.model_validate_json(line))
        except ValidationError:
            continue
    return entries[-limit:] if limit > 0 else []
