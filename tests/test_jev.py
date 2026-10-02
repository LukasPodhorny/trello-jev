"""JevClassifier tests: real System One wiring against mocked HTTP."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from t.classify.base import Decision
from t.classify.jev import (
    JevAuthError,
    JevClassifier,
    JevError,
    JevRateLimitError,
    SystemOneResponse,
    build_questions,
    decision_from_answers,
)
from t.classify.schema import InvalidDecisionError, StructureSchema, build_schema
from t.cli import app
from tests.test_classify_add import sample_cache

runner = CliRunner()
JEV_URL = "https://api.typesafe.ai/v1/systemone"


def jev_response(
    board: str = "b1",
    board_conf: float = 0.9,
    lst: str = "l1",
    list_conf: float = 0.85,
    list_probs: dict[str, float] | None = None,
    labels: dict[str, float] | None = None,
) -> dict[str, object]:
    answers: dict[str, object] = {
        "board": {
            "type": "choice",
            "choice": board,
            "probabilities": {"b1": 0.9, "b2": 0.1},
            "confidence": board_conf,
        },
        "list": {
            "type": "choice",
            "choice": lst,
            "probabilities": list_probs or {"l1": 0.8, "l2": 0.15, "l3": 0.05},
            "confidence": list_conf,
        },
    }
    for label_id, noul in (labels or {}).items():
        answers[f"label:{label_id}"] = {"type": "noul", "noul": noul}
    return {"model": "jev-1.13.0", "answers": answers, "usage": {}}


def make_classifier(**kwargs: object) -> JevClassifier:
    return JevClassifier("secret", backoff_base=0.0, **kwargs)  # type: ignore[arg-type]


@respx.mock
def test_decide_happy_path() -> None:
    route = respx.post(JEV_URL).mock(
        return_value=httpx.Response(200, json=jev_response(labels={"lb1": 0.9, "lb2": 0.1}))
    )
    schema = build_schema(sample_cache())
    with make_classifier() as jev:
        decision = jev.decide("domluvit konzultaci k projektu, do pátku", schema)
    assert isinstance(decision, Decision)
    schema.validate_decision(decision)
    assert (decision.board_id, decision.list_id) == ("b1", "l1")
    assert decision.label_ids == ["lb1"]
    assert decision.due_phrase == "do pátku"
    assert "pátku" not in decision.title
    assert decision.confidence == 0.85  # min(0.9 board, 0.85 list)

    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer secret"
    import json

    body = json.loads(request.content.decode())
    assert body["state"] == "domluvit konzultaci k projektu, do pátku"
    assert body["model"] == "jev-latest"
    assert set(body["questions"]) == {"board", "list", "label:lb1", "label:lb2"}
    assert body["questions"]["board"]["criteria"] == {"b1": "Work", "b2": "Home"}


@respx.mock
def test_decide_reconciles_cross_board_list() -> None:
    respx.post(JEV_URL).mock(
        return_value=httpx.Response(
            200,
            json=jev_response(board="b1", lst="l3", list_probs={"l3": 0.7, "l1": 0.2, "l2": 0.1}),
        )
    )
    schema = build_schema(sample_cache())
    with make_classifier() as jev:
        decision = jev.decide("něco", schema)
    # l3 is on Home; best list of Work by probability is l1.
    assert (decision.board_id, decision.list_id) == ("b1", "l1")


def test_decision_from_answers_unknown_board() -> None:
    schema = build_schema(sample_cache())
    payload = SystemOneResponse.model_validate(jev_response(board="bx"))
    with pytest.raises(InvalidDecisionError):
        decision_from_answers(payload.answers, schema, title="t", due_phrase=None)


def test_decision_from_answers_missing() -> None:
    schema = build_schema(sample_cache())
    with pytest.raises(InvalidDecisionError):
        decision_from_answers({}, schema, title="t", due_phrase=None)


def test_build_questions_empty_schema() -> None:
    with pytest.raises(JevError):
        build_questions(StructureSchema())


@respx.mock
def test_401_raises_auth_error() -> None:
    respx.post(JEV_URL).mock(return_value=httpx.Response(401, json={"error": "no"}))
    with make_classifier() as jev:
        with pytest.raises(JevAuthError):
            jev.decide("něco", build_schema(sample_cache()))


@respx.mock
def test_422_raises_jev_error() -> None:
    respx.post(JEV_URL).mock(return_value=httpx.Response(422, json={"error": "bad"}))
    with make_classifier() as jev:
        with pytest.raises(JevError):
            jev.decide("něco", build_schema(sample_cache()))


@respx.mock
def test_429_retries_then_succeeds() -> None:
    route = respx.post(JEV_URL)
    route.side_effect = [
        httpx.Response(429, json="slow"),
        httpx.Response(529, json="busy"),
        httpx.Response(200, json=jev_response()),
    ]
    with make_classifier() as jev:
        decision = jev.decide("něco", build_schema(sample_cache()))
    assert decision.board_id == "b1"
    assert route.call_count == 3


@respx.mock
def test_429_exhausted_raises() -> None:
    route = respx.post(JEV_URL)
    route.side_effect = [httpx.Response(429, json="slow")] * 3
    with make_classifier() as jev:
        with pytest.raises(JevRateLimitError):
            jev.decide("něco", build_schema(sample_cache()))
    assert route.call_count == 3


def test_missing_key_raises_without_http() -> None:
    with JevClassifier(api_key=None, backoff_base=0.0) as jev:
        with pytest.raises(JevAuthError):
            jev.decide("něco", build_schema(sample_cache()))


@respx.mock
def test_ping_ok_and_unauthorized() -> None:
    respx.post(JEV_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"ok": {"type": "noul", "noul": 0.0}},
                "usage": {},
            },
        )
    )
    with make_classifier() as jev:
        assert jev.ping() is True


@respx.mock
def test_ping_401() -> None:
    respx.post(JEV_URL).mock(return_value=httpx.Response(401, json={}))
    with make_classifier() as jev:
        with pytest.raises(JevAuthError):
            jev.ping()


def mock_trello_for_sample_cache() -> None:
    respx.get("https://api.trello.com/1/members/me/boards").mock(
        return_value=httpx.Response(200, json=[{"id": "b1", "name": "Work"}])
    )
    respx.get("https://api.trello.com/1/boards/b1/lists").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "l1", "name": "Todo", "idBoard": "b1"},
                {"id": "l2", "name": "Inbox", "idBoard": "b1"},
            ],
        )
    )
    respx.get("https://api.trello.com/1/boards/b1/labels").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "lb1", "name": "urgent", "color": "red"},
                {"id": "lb2", "name": "k roztřídění", "color": "yellow"},
            ],
        )
    )


@respx.mock
def test_add_jev_dry_run_returns_valid_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "tok")
    monkeypatch.setenv("JEV_API_KEY", "secret")
    mock_trello_for_sample_cache()
    respx.post(JEV_URL).mock(return_value=httpx.Response(200, json=jev_response()))
    result = runner.invoke(
        app,
        [
            "add",
            "domluvit konzultaci k projektu, do pátku",
            "--dry-run",
            "--classifier",
            "jev",
            "--date-engine",
            "parser",
            "--cache-path",
            str(tmp_path / "structure.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Work → Todo" in result.output
