"""MockClassifier: deterministic keyword rules, no network, no model.

Picks the board/list/labels by matching words from the schema against the
input text and extracts a raw Czech/English due phrase for ``dates.py``
(M4). Used for development and as a fallback when Jev is unreachable.
"""

from __future__ import annotations

import re

from t.classify.base import Decision
from t.classify.phrases import extract_due_phrase
from t.classify.schema import BoardSchema, StructureSchema

URGENT_WORDS = frozenset({"urgent", "urgentní", "urgentne", "asap", "spěchá", "hoří", "priorita"})
URGENT_LABEL_HINTS = ("urgent", "priorit", "důležit", "asap")


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _board_for(board: BoardSchema, words: set[str]) -> float:
    name_words = {w for w in _words(board.name) if len(w) >= 3}
    return len(name_words & words)


class MockClassifier:
    """Rule-based Classifier over a StructureSchema."""

    def decide(self, text: str, schema: StructureSchema) -> Decision:
        if not schema.boards:
            raise ValueError("MockClassifier needs a non-empty schema.")
        words = _words(text)
        remainder, due_phrase = extract_due_phrase(text.strip())
        title = remainder.strip(" \"'“”") or text.strip()

        # Board: prefer a board whose name shows up in the text.
        scored = sorted(schema.boards, key=lambda b: _board_for(b, words), reverse=True)
        board = scored[0]
        board_hit = _board_for(board, words) > 0

        # List: prefer a list whose name shows up; else the first list.
        chosen = board.lists[0] if board.lists else None
        list_hit = False
        for lst in board.lists:
            name_words = {w for w in _words(lst.name) if len(w) >= 3}
            if name_words & words:
                chosen = lst
                list_hit = True
                break
        if chosen is None:  # board without lists: still a valid low-confidence pick
            raise ValueError(f"Board {board.name!r} has no lists to place the card in.")

        # Labels: urgent words map to urgent-like labels; label names in text match.
        label_ids: list[str] = []
        if words & URGENT_WORDS:
            for label in board.labels:
                if any(h in label.name.lower() for h in URGENT_LABEL_HINTS):
                    label_ids.append(label.id)
        for label in board.labels:
            if (
                label.id not in label_ids
                and len(label.name) >= 3
                and label.name.lower() in text.lower()
            ):
                label_ids.append(label.id)

        confidence = 0.5
        if due_phrase:
            confidence += 0.15
        if list_hit:
            confidence += 0.2
        if board_hit and len(schema.boards) > 1:
            confidence += 0.1
        confidence = round(min(confidence, 0.95), 2)

        return Decision(
            board_id=board.id,
            list_id=chosen.id,
            label_ids=label_ids,
            title=title,
            description=None,
            due_phrase=due_phrase,
            confidence=confidence,
        )
