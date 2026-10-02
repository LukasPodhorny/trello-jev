"""M4 tests: date parsing, Inbox fallback, card creation, decision log."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from typer.testing import CliRunner

from t import config as config_mod
from t.classify.base import Decision
from t.classify.schema import SchemaError, build_schema
from t.cli import app, apply_low_confidence_fallback
from t.dates import PRAGUE, parse_due
from t.decision_log import DecisionLogEntry, append_log, read_last
from t.trello import cache as cache_mod
from t.trello.cache import BoardStructure, CachedStructure
from t.trello.models import Board, TrelloList
from tests.test_classify_add import sample_cache

runner = CliRunner()
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=ZoneInfo("Europe/Prague"))  # a Thursday


def test_parse_v_patek() -> None:
    due = parse_due("v pátek", NOW)
    assert due is not None
    assert (due.year, due.month, due.day) == (2026, 10, 2)  # next day is Friday
    assert due.tzinfo is not None


def test_parse_do_patku() -> None:
    # The PLAN §1 example: "do pátku" is a deadline, same due date as "v pátek".
    due = parse_due("do pátku", NOW)
    assert due is not None
    assert (due.year, due.month, due.day) == (2026, 10, 2)
    assert due.tzinfo is not None


def test_parse_do_ctvrtka() -> None:
    due = parse_due("do čtvrtka", NOW)
    assert due is not None
    assert (due.year, due.month, due.day) == (2026, 10, 8)  # next Thursday
    assert due.tzinfo is not None


def test_parse_za_dva_tydny() -> None:
    due = parse_due("za dva týdny", NOW)
    assert due is not None
    assert (due.year, due.month, due.day) == (2026, 10, 15)


def test_parse_zitra_v_15() -> None:
    due = parse_due("zítra v 15:00", NOW)
    assert due is not None
    assert (due.year, due.month, due.day, due.hour, due.minute) == (
        2026,
        10,
        2,
        15,
        0,
    )


def test_parse_none_and_garbage() -> None:
    assert parse_due(None, NOW) is None
    assert parse_due("   ", NOW) is None
    assert parse_due("blafy xyzzy", NOW) is None


def low_confidence_decision() -> Decision:
    return Decision(
        board_id="b1",
        list_id="l1",
        label_ids=[],
        title="nejasný úkol",
        confidence=0.3,
    )


def test_fallback_routes_to_inbox() -> None:
    schema = build_schema(sample_cache())
    out = apply_low_confidence_fallback(low_confidence_decision(), schema, config_mod.Settings())
    assert out.list_id == "l2"  # Inbox on Work
    assert "lb2" in out.label_ids  # 'k roztřídění' attached


def test_fallback_passes_high_confidence() -> None:
    schema = build_schema(sample_cache())
    decision = low_confidence_decision().model_copy(update={"confidence": 0.9, "list_id": "l1"})
    out = apply_low_confidence_fallback(decision, schema, config_mod.Settings())
    assert out.list_id == "l1"


def test_fallback_missing_inbox_errors() -> None:
    cached = CachedStructure(
        fetched_at=cache_mod.utcnow(),
        boards=[
            BoardStructure(
                board=Board(id="b1", name="Work"),
                lists=[TrelloList(id="l1", name="Todo", idBoard="b1")],
                labels=[],
            )
        ],
    )
    with pytest.raises(SchemaError, match="t init"):
        apply_low_confidence_fallback(
            low_confidence_decision(), build_schema(cached), config_mod.Settings()
        )


def mock_board_with_inbox() -> None:
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
def test_add_creates_card_and_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "tok")
    monkeypatch.setenv("T_CACHE_PATH", str(tmp_path))
    monkeypatch.setenv("T_LOG_PATH", str(tmp_path))
    mock_board_with_inbox()
    post = respx.post("https://api.trello.com/1/cards").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "c1",
                "name": "neurčitý úkol",
                "idList": "l2",
                "url": "https://trello.com/c/c1",
            },
        )
    )
    result = runner.invoke(
        app,
        [
            "add",
            "neurčitý úkol",
            "--yes",
            "--date-engine",
            "parser",
            "--cache-path",
            str(tmp_path / "s.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "trello.com/c/c1" in result.output
    # Low mock confidence (0.5 < 0.6) → Inbox fallback kicked in.
    assert post.calls[0].request.url.params["idList"] == "l2"
    assert "Low confidence" in result.output
    entries = read_last(tmp_path / "decision_log.jsonl", 10)
    assert len(entries) == 1
    assert entries[0].card_id == "c1"
    assert entries[0].input_text == "neurčitý úkol"


@respx.mock
def test_add_confirmation_abort(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "tok")
    mock_board_with_inbox()
    post = respx.post("https://api.trello.com/1/cards").mock(
        return_value=httpx.Response(200, json={"id": "c1", "name": "x", "idList": "l1"})
    )
    result = runner.invoke(
        app,
        [
            "add",
            "urgent: fixnout inbox",
            "--date-engine",
            "parser",
            "--cache-path",
            str(tmp_path / "s.json"),
        ],
        input="n\n",
    )
    assert result.exit_code != 0
    assert len(post.calls) == 0  # aborted: nothing created


def test_log_command_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("T_LOG_PATH", str(tmp_path))
    result = runner.invoke(app, ["log"])
    assert result.exit_code == 0
    assert "No decisions" in result.output


def test_log_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "decision_log.jsonl"
    entry = DecisionLogEntry(
        ts=datetime(2026, 10, 1, 10, 0, tzinfo=PRAGUE),
        input_text="ahoj",
        decision=low_confidence_decision(),
        card_id="c9",
    )
    append_log(entry, path)
    assert path.exists()
    last = read_last(path, 5)
    assert len(last) == 1 and last[0].card_id == "c9"
    # Corrupt lines are skipped, not fatal.
    with path.open("a", encoding="utf-8") as f:
        f.write("{broken\n")
    assert len(read_last(path, 5)) == 1
