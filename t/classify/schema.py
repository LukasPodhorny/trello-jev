"""Dynamic StructureSchema built from the Trello cache.

The schema carries real board/list/label names *and* IDs; the classifier
returns IDs and they are validated back against this schema. An unknown ID
is an error, never silently fixed.
"""

from __future__ import annotations

from pydantic import BaseModel

from t.trello.cache import CachedStructure


class SchemaError(Exception):
    """The cache cannot produce the requested schema (--board mismatch etc.)."""


class InvalidDecisionError(Exception):
    """A Decision references IDs outside the schema."""


class LabelSchema(BaseModel):
    id: str
    name: str
    color: str | None = None


class ListSchema(BaseModel):
    id: str
    name: str


class BoardSchema(BaseModel):
    id: str
    name: str
    lists: list[ListSchema] = []
    labels: list[LabelSchema] = []


class StructureSchema(BaseModel):
    boards: list[BoardSchema] = []

    def board(self, board_id: str) -> BoardSchema:
        for b in self.boards:
            if b.id == board_id:
                return b
        raise InvalidDecisionError(f"Unknown board_id {board_id!r}.")

    def validate_decision(self, decision: object) -> None:
        """Raise InvalidDecisionError if any referenced ID is not in the schema."""
        from t.classify.base import Decision

        if not isinstance(decision, Decision):
            raise InvalidDecisionError(
                f"Decision must be a Decision model, got {type(decision).__name__}."
            )
        board = self.board(decision.board_id)
        if all(lst.id != decision.list_id for lst in board.lists):
            raise InvalidDecisionError(
                f"Unknown list_id {decision.list_id!r} for board {board.name!r}."
            )
        known_labels = {label.id for label in board.labels}
        for label_id in decision.label_ids:
            if label_id not in known_labels:
                raise InvalidDecisionError(
                    f"Unknown label_id {label_id!r} for board {board.name!r}."
                )


def build_schema(cached: CachedStructure, board_name: str | None = None) -> StructureSchema:
    """Build the schema from cache, optionally restricted to one board name."""
    boards = [
        BoardSchema(
            id=b.board.id,
            name=b.board.name,
            lists=[ListSchema(id=lst.id, name=lst.name) for lst in b.lists],
            labels=[LabelSchema(id=lb.id, name=lb.name, color=lb.color) for lb in b.labels],
        )
        for b in cached.boards
    ]
    if board_name is None:
        return StructureSchema(boards=boards)
    matches = [b for b in boards if b.name.lower() == board_name.lower()]
    if not matches:
        available = ", ".join(b.name for b in boards) or "(no boards cached)"
        raise SchemaError(f"Board {board_name!r} not in cache. Available: {available}.")
    if len(matches) > 1:
        raise SchemaError(
            f"Board name {board_name!r} is ambiguous "
            f"({len(matches)} boards share it); narrow it down in `t init`."
        )
    return StructureSchema(boards=matches)
