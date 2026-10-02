"""M2 tests: schema from cache, MockClassifier decisions, ``t add --dry-run``."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from t.classify.base import Decision
from t.classify.mock import MockClassifier
from t.classify.schema import (
    InvalidDecisionError,
    SchemaError,
    build_schema,
)
from t.cli import app
from t.trello import cache as cache_mod
from t.trello.cache import BoardStructure, CachedStructure
from t.trello.models import Board, Label, TrelloList

runner = CliRunner()


def sample_cache() -> CachedStructure:
    return CachedStructure(
        fetched_at=cache_mod.utcnow(),
        boards=[
            BoardStructure(
                board=Board(id="b1", name="Work"),
                lists=[
                    TrelloList(id="l1", name="Todo", idBoard="b1"),
                    TrelloList(id="l2", name="Inbox", idBoard="b1"),
                ],
                labels=[
                    Label(id="lb1", name="urgent", color="red"),
                    Label(id="lb2", name="k roztřídění", color="yellow"),
                ],
            ),
            BoardStructure(
                board=Board(id="b2", name="Home"),
                lists=[TrelloList(id="l3", name="Todo", idBoard="b2")],
                labels=[],
            ),
        ],
    )


def valid_decision() -> Decision:
    return Decision(
        board_id="b1",
        list_id="l1",
        label_ids=["lb1"],
        title="Do the thing",
        due_phrase="do pátku",
        confidence=0.8,
    )


def test_build_schema_from_cache() -> None:
    schema = build_schema(sample_cache())
    assert [b.name for b in schema.boards] == ["Work", "Home"]
    work = schema.board("b1")
    assert [lst.name for lst in work.lists] == ["Todo", "Inbox"]
    assert [lb.name for lb in work.labels] == ["urgent", "k roztřídění"]


def test_build_schema_board_filter() -> None:
    schema = build_schema(sample_cache(), "work")
    assert [b.name for b in schema.boards] == ["Work"]


def test_build_schema_unknown_board() -> None:
    with pytest.raises(SchemaError):
        build_schema(sample_cache(), "Nope")


def test_validate_decision_ok() -> None:
    build_schema(sample_cache()).validate_decision(valid_decision())


def test_validate_decision_bad_ids() -> None:
    schema = build_schema(sample_cache())
    for bad in [
        valid_decision().model_copy(update={"board_id": "bx"}),
        valid_decision().model_copy(update={"list_id": "l3"}),  # l3 is on Home
        valid_decision().model_copy(update={"label_ids": ["lbX"]}),
    ]:
        with pytest.raises(InvalidDecisionError):
            schema.validate_decision(bad)


def test_validate_decision_wrong_type() -> None:
    with pytest.raises(InvalidDecisionError):
        schema = build_schema(sample_cache())
        schema.validate_decision(object())


def test_mock_classifier_example_sentence() -> None:
    schema = build_schema(sample_cache())
    decision = MockClassifier().decide("domluvit konzultaci k projektu, do pátku", schema)
    schema.validate_decision(decision)  # must be schema-valid
    assert decision.board_id == "b1"
    assert decision.due_phrase is not None and "pát" in decision.due_phrase
    assert "pát" not in decision.title  # title cleaned of the date
    assert 0.0 <= decision.confidence <= 1.0


def test_mock_classifier_list_keyword_and_labels() -> None:
    schema = build_schema(sample_cache())
    decision = MockClassifier().decide("urgent: fixnout inbox projektu", schema)
    assert decision.list_id == "l2"  # "inbox" matched the list name
    assert decision.label_ids == ["lb1"]  # urgent label attached
    assert decision.due_phrase is None


def test_mock_classifier_empty_schema() -> None:
    with pytest.raises(ValueError):
        MockClassifier().decide(
            "cokoliv", build_schema(CachedStructure(fetched_at=cache_mod.utcnow()))
        )


def mock_http_structure() -> None:
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
        return_value=httpx.Response(200, json=[{"id": "lb1", "name": "urgent", "color": "red"}])
    )


@respx.mock
def test_add_dry_run_from_real_cache_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "tok")
    mock_http_structure()
    cache = tmp_path / "structure.json"
    result = runner.invoke(
        app,
        [
            "add",
            "domluvit konzultaci k projektu, do pátku",
            "--dry-run",
            "--date-engine",
            "parser",
            "--cache-path",
            str(cache),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Work → Todo" in result.output
    assert "Dry run" in result.output
    assert cache.exists()  # missing cache was synced first


@respx.mock
def test_add_unknown_board_option(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "tok")
    mock_http_structure()
    result = runner.invoke(
        app,
        [
            "add",
            "něco",
            "--dry-run",
            "--board",
            "Nope",
            "--cache-path",
            str(tmp_path / "structure.json"),
        ],
    )
    assert result.exit_code == 1
    assert "Nope" in result.output


def test_add_jev_without_key_errors_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "tok")
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    cache = tmp_path / "structure.json"
    cache_mod.save_cache(sample_cache(), cache)
    result = runner.invoke(
        app, ["add", "něco", "--dry-run", "--classifier", "jev", "--cache-path", str(cache)]
    )
    assert result.exit_code == 1
    assert "JEV_API_KEY" in result.output
