"""JevClassifier: decisions from TypeSafe's System One model (Jev).

One ``POST /v1/systemone`` call per decision (speculative fan-out):
a ``board`` Choice, a ``list`` Choice over all lists (qualified as
``Board → List`` so same-named lists stay distinct), and one Noul per label.
See https://docs.typesafe.ai/api

With ``decide_with_dates`` the same request also carries the seven date-part
Choices; ``t.classify.jev_dates`` turns the answers into a date in code.
Title/due_phrase splitting stays code (``t.classify.phrases``).
"""

from __future__ import annotations

import time
from datetime import date
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from t.classify.base import Decision
from t.classify.jev_dates import (
    DATE_PART_NAMES,
    DatePart,
    JevDateResult,
    assemble,
    build_date_question_specs,
)
from t.classify.phrases import extract_due_phrase
from t.classify.schema import BoardSchema, InvalidDecisionError, StructureSchema

JEV_API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"

#: Noul value at or above which a label is attached.
LABEL_NOUL_THRESHOLD = 0.5

#: Retry budget for 429/529, mirroring the Trello client.
MAX_ATTEMPTS = 3


class JevError(Exception):
    """Base class for Jev failures (never carries the API key)."""


class JevAuthError(JevError):
    """Missing or invalid JEV_API_KEY (or HTTP 401)."""


class JevRateLimitError(JevError):
    """Still rate limited/overloaded after exhausting retries."""


class ChoiceQuestion(BaseModel):
    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str | None]


class NoulQuestion(BaseModel):
    type: Literal["noul"] = "noul"
    instructions: str


class ChoiceAnswer(BaseModel):
    type: Literal["choice"] = "choice"
    choice: str
    probabilities: dict[str, float] = {}
    confidence: float = Field(ge=0.0, le=1.0)


class NoulAnswer(BaseModel):
    type: Literal["noul"] = "noul"
    noul: float = Field(ge=0.0, le=1.0)


class SystemOneResponse(BaseModel):
    model: str = ""
    answers: dict[str, ChoiceAnswer | NoulAnswer] = {}


def build_questions(schema: StructureSchema) -> dict[str, ChoiceQuestion | NoulQuestion]:
    """Build one fan-out question set from the schema (IDs as option keys)."""
    if not schema.boards:
        raise JevError("JevClassifier needs a non-empty schema.")
    questions: dict[str, ChoiceQuestion | NoulQuestion] = {
        "board": ChoiceQuestion(
            instructions="Which board should this task go to?",
            criteria={b.id: b.name for b in schema.boards},
        )
    }
    list_criteria: dict[str, str | None] = {}
    for board in schema.boards:
        for lst in board.lists:
            list_criteria[lst.id] = f"{board.name} → {lst.name}"
    if not list_criteria:
        raise JevError("JevClassifier needs at least one list in the schema.")
    if len(list_criteria) > 255:
        raise JevError(
            f"Too many lists ({len(list_criteria)}, API allows 255 per Choice); "
            "narrow the schema with `t add --board <name>`."
        )
    questions["list"] = ChoiceQuestion(
        instructions="Which list should hold this task?",
        criteria=list_criteria,
    )
    for board in schema.boards:
        for label in board.labels:
            name = label.name or f"color {label.color or '?'}"
            questions[f"label:{label.id}"] = NoulQuestion(
                instructions=(f"Should the task get the label '{name}' on board '{board.name}'?")
            )
    return questions


def decision_from_answers(
    answers: dict[str, ChoiceAnswer | NoulAnswer],
    schema: StructureSchema,
    *,
    title: str,
    due_phrase: str | None,
) -> Decision:
    """Turn raw System One answers into a schema-validated Decision.

    The list Choice spans all boards, so a list pick outside the chosen
    board is reconciled to the highest-probability list *of that board*.
    Unknown IDs are errors, never silently fixed.
    """
    board_answer = answers.get("board")
    list_answer = answers.get("list")
    if not isinstance(board_answer, ChoiceAnswer):
        raise InvalidDecisionError("Jev answer 'board' is missing or not a Choice.")
    if not isinstance(list_answer, ChoiceAnswer):
        raise InvalidDecisionError("Jev answer 'list' is missing or not a Choice.")
    board = schema.board(board_answer.choice)  # raises on unknown board_id
    list_id = _reconcile_list(list_answer, board)
    label_ids: list[str] = []
    for label in board.labels:
        label_answer = answers.get(f"label:{label.id}")
        if isinstance(label_answer, NoulAnswer) and label_answer.noul >= LABEL_NOUL_THRESHOLD:
            label_ids.append(label.id)
    confidence = round(min(board_answer.confidence, list_answer.confidence), 2)
    decision = Decision(
        board_id=board.id,
        list_id=list_id,
        label_ids=label_ids,
        title=title,
        description=None,
        due_phrase=due_phrase,
        confidence=confidence,
    )
    schema.validate_decision(decision)
    return decision


def build_date_questions(today: date) -> dict[str, ChoiceQuestion | NoulQuestion]:
    """Wrap the date-part specs as wire questions (no key collides with the
    classification questions: ``board``/``list``/``label:*``)."""
    return {
        name: ChoiceQuestion(instructions=spec.instructions, criteria=spec.criteria)
        for name, spec in build_date_question_specs(today).items()
    }


def parts_from_answers(
    answers: dict[str, ChoiceAnswer | NoulAnswer],
) -> dict[str, DatePart]:
    """Pick the seven date parts out of a fan-out answer map.

    A missing (or non-Choice) date answer degrades to ``none`` at 0.0
    confidence: downstream that yields no date plus a reason, never a crash
    and never a guess.
    """
    parts: dict[str, DatePart] = {}
    for name in DATE_PART_NAMES:
        answer = answers.get(name)
        if isinstance(answer, ChoiceAnswer):
            parts[name] = DatePart(choice=answer.choice, confidence=answer.confidence)
        else:
            parts[name] = DatePart(choice="none", confidence=0.0)
    return parts


def _reconcile_list(list_answer: ChoiceAnswer, board: BoardSchema) -> str:
    """Pick the model's list when it sits on the board, else the board's best."""
    on_board = {lst.id for lst in board.lists}
    if list_answer.choice in on_board:
        return list_answer.choice
    ranked = sorted(
        ((prob, lid) for lid, prob in list_answer.probabilities.items() if lid in on_board),
        reverse=True,
    )
    if not ranked:
        raise InvalidDecisionError(
            f"Jev picked list {list_answer.choice!r}, which is not on board "
            f"{board.name!r}, and gave no usable alternative."
        )
    return ranked[0][1]


class JevClassifier:
    """Classifier delegating the decision to the Jev model.

    Sends ``state`` + one fan-out question set per decision; the API key
    travels in the ``Authorization: Bearer`` header (never in logs/URLs).
    """

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        backoff_base: float = 1.0,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._backoff_base = backoff_base
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JevClassifier:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def decide(self, text: str, schema: StructureSchema) -> Decision:
        questions = build_questions(schema)
        remainder, due_phrase = extract_due_phrase(text.strip())
        title = remainder.strip(" \"'“”") or text.strip()
        payload = self._evaluate(text, questions)
        try:
            response = SystemOneResponse.model_validate(payload)
        except ValueError as e:
            raise JevError(f"Jev returned an unreadable response: {e}") from e
        return decision_from_answers(response.answers, schema, title=title, due_phrase=due_phrase)

    def decide_with_dates(
        self, text: str, schema: StructureSchema, today: date
    ) -> tuple[Decision, JevDateResult]:
        """Classify *and* read date parts in one fan-out request."""
        questions: dict[str, ChoiceQuestion | NoulQuestion] = build_questions(schema)
        questions.update(build_date_questions(today))
        remainder, due_phrase = extract_due_phrase(text.strip())
        title = remainder.strip(" \"'“”") or text.strip()
        payload = self._evaluate(text, questions)
        try:
            response = SystemOneResponse.model_validate(payload)
        except ValueError as e:
            raise JevError(f"Jev returned an unreadable response: {e}") from e
        decision = decision_from_answers(
            response.answers, schema, title=title, due_phrase=due_phrase
        )
        return decision, assemble(parts_from_answers(response.answers), today)

    def extract_date(self, text: str, today: date) -> JevDateResult:
        """Read date parts with a date-questions-only request (used when the
        classifier itself is not Jev)."""
        payload = self._evaluate(text, build_date_questions(today))
        try:
            response = SystemOneResponse.model_validate(payload)
        except ValueError as e:
            raise JevError(f"Jev returned an unreadable response: {e}") from e
        return assemble(parts_from_answers(response.answers), today)

    def ping(self) -> bool:
        """Minimal paid call proving the key works and the API is reachable."""
        payload = self._evaluate(
            "ping",
            {"ok": NoulQuestion(instructions="Is this a connectivity check?")},
        )
        try:
            _ = SystemOneResponse.model_validate(payload)
        except ValueError as e:
            raise JevError(f"Jev returned an unreadable response: {e}") from e
        return True

    def _evaluate(self, state: str, questions: dict[str, ChoiceQuestion | NoulQuestion]) -> object:
        if not self._api_key:
            raise JevAuthError("JEV_API_KEY is not set. Export it or add it to .env; see SETUP.md.")
        body = {
            "state": state,
            "model": self._model,
            "questions": {name: question.model_dump() for name, question in questions.items()},
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_status = 0
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._client.post(JEV_API_URL, json=body, headers=headers)
            except httpx.HTTPError as e:
                raise JevError(f"Jev API unreachable: {e}") from e
            last_status = response.status_code
            if response.status_code in (429, 529):
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(self._backoff_base * (2.0**attempt))
                    continue
                raise JevRateLimitError(
                    f"Jev rate limited (HTTP {last_status}) "
                    f"after {MAX_ATTEMPTS} attempts; try again later."
                )
            if response.status_code == 401:
                raise JevAuthError("Jev rejected the API key (HTTP 401). Check JEV_API_KEY.")
            if response.status_code == 422:
                raise JevError(f"Jev rejected the request (HTTP 422): {response.text}")
            if response.status_code >= 400:
                raise JevError(f"Jev request failed (HTTP {response.status_code}): {response.text}")
            return response.json()
        raise JevRateLimitError(  # pragma: no cover - loop always returns/raises
            f"Jev rate limited (HTTP {last_status})."
        )
