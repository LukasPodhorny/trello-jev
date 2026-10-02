"""Jev date engine: pure assemble tests (pinned TODAY) + fake-client wiring."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from typer.testing import CliRunner

from t import config as config_mod
from t.classify.jev import (
    ChoiceAnswer,
    ChoiceQuestion,
    JevClassifier,
    NoulAnswer,
    NoulQuestion,
)
from t.classify.jev_dates import (
    DatePart,
    JevDateResult,
    assemble,
    build_date_question_specs,
    resolve_weekday,
)
from t.cli import app, apply_date_gate
from t.decision_log import read_last
from tests.test_classify_add import sample_cache
from tests.test_jev import jev_response, mock_trello_for_sample_cache

runner = CliRunner()
TODAY = date(2026, 10, 1)  # a Thursday
PRAGUE = ZoneInfo("Europe/Prague")


def make_parts(overrides: dict[str, tuple[str, float]]) -> dict[str, DatePart]:
    parts = {
        name: DatePart(choice="none", confidence=0.99)
        for name in (
            "mode",
            "month",
            "day",
            "year",
            "day_anchor",
            "weekday",
            "week_offset",
        )
    }
    for name, (choice, conf) in overrides.items():
        parts[name] = DatePart(choice=choice, confidence=conf)
    return parts


# -- pure assemble -------------------------------------------------------


def test_assemble_absolute_with_year() -> None:
    result = assemble(
        make_parts(
            {
                "mode": ("absolute", 0.95),
                "month": ("October", 0.9),
                "day": ("15", 0.92),
                "year": ("2026", 0.88),
            }
        ),
        TODAY,
    )
    assert result.date == date(2026, 10, 15)
    assert result.confidence == 0.88


def test_assemble_absolute_without_year_no_bump() -> None:
    result = assemble(
        make_parts(
            {
                "mode": ("absolute", 0.95),
                "month": ("October", 0.9),
                "day": ("20", 0.92),
                "year": ("none", 0.88),
            }
        ),
        TODAY,
    )
    assert result.date == date(2026, 10, 20)


def test_assemble_absolute_without_year_bump() -> None:
    # 2026-08-14 is more than 31 days past 2026-10-01 → next year.
    result = assemble(
        make_parts(
            {
                "mode": ("absolute", 0.95),
                "month": ("August", 0.9),
                "day": ("14", 0.92),
                "year": ("none", 0.88),
            }
        ),
        TODAY,
    )
    assert result.date == date(2027, 8, 14)


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        ("today", date(2026, 10, 1)),
        ("tomorrow", date(2026, 10, 2)),
        ("day_after", date(2026, 10, 3)),
    ],
)
def test_assemble_day_anchors(anchor: str, expected: date) -> None:
    result = assemble(
        make_parts({"mode": ("relative", 0.9), "day_anchor": (anchor, 0.85)}),
        TODAY,
    )
    assert result.date == expected
    assert result.confidence == 0.85


def test_assemble_bare_weekday() -> None:
    friday = assemble(
        make_parts(
            {
                "mode": ("relative", 0.9),
                "day_anchor": ("weekday", 0.85),
                "weekday": ("Friday", 0.8),
                "week_offset": ("none", 0.75),
            }
        ),
        TODAY,
    )
    assert friday.date == date(2026, 10, 2)  # next occurrence on or after today
    assert friday.confidence == 0.75
    # Same weekday as today resolves to today itself.
    thursday = assemble(
        make_parts(
            {
                "mode": ("relative", 0.9),
                "day_anchor": ("weekday", 0.85),
                "weekday": ("Thursday", 0.8),
                "week_offset": ("none", 0.75),
            }
        ),
        TODAY,
    )
    assert thursday.date == TODAY


def test_assemble_next_and_current_weekday() -> None:
    nxt = assemble(
        make_parts(
            {
                "mode": ("relative", 0.9),
                "day_anchor": ("weekday", 0.85),
                "weekday": ("Thursday", 0.8),
                "week_offset": ("next", 0.75),
            }
        ),
        TODAY,
    )
    assert nxt.date == date(2026, 10, 8)  # following calendar week
    cur = assemble(
        make_parts(
            {
                "mode": ("relative", 0.9),
                "day_anchor": ("weekday", 0.85),
                "weekday": ("Thursday", 0.8),
                "week_offset": ("current", 0.75),
            }
        ),
        TODAY,
    )
    assert cur.date == date(2026, 10, 1)  # this week (may be past: see below)
    monday = assemble(
        make_parts(
            {
                "mode": ("relative", 0.9),
                "day_anchor": ("weekday", 0.85),
                "weekday": ("Monday", 0.8),
                "week_offset": ("current", 0.75),
            }
        ),
        TODAY,
    )
    assert monday.date == date(2026, 9, 28)  # 'current' locks this week's Monday


def test_assemble_impossible_date() -> None:
    no_year = assemble(
        make_parts(
            {
                "mode": ("absolute", 0.9),
                "month": ("February", 0.9),
                "day": ("30", 0.9),
                "year": ("none", 0.9),
            }
        ),
        TODAY,
    )
    assert no_year.date is None
    assert "impossible" in no_year.note
    with_year = assemble(
        make_parts(
            {
                "mode": ("absolute", 0.9),
                "month": ("February", 0.9),
                "day": ("29", 0.9),
                "year": ("2026", 0.9),
            }
        ),
        TODAY,
    )
    assert with_year.date is None  # 2026 is not a leap year


def test_assemble_no_date() -> None:
    result = assemble(make_parts({"mode": ("none", 0.93)}), TODAY)
    assert result.date is None
    assert result.mode == "none"
    assert result.confidence == 0.93


def test_assemble_confidence_ignores_irrelevant_parts() -> None:
    result = assemble(
        make_parts(
            {
                "mode": ("absolute", 0.9),
                "month": ("October", 0.85),
                "day": ("15", 0.42),
                "year": ("none", 0.8),
                "weekday": ("Friday", 0.05),  # irrelevant to absolute mode
                "week_offset": ("next", 0.05),
            }
        ),
        TODAY,
    )
    assert result.date == date(2026, 10, 15)
    assert result.confidence == 0.42  # min over used parts only


def test_assemble_out_of_range_year() -> None:
    result = assemble(
        make_parts(
            {
                "mode": ("absolute", 0.9),
                "month": ("October", 0.9),
                "day": ("15", 0.9),
                "year": ("out_of_range", 0.9),
            }
        ),
        TODAY,
    )
    assert result.date is None
    assert "2026-2028" in result.note


def test_assemble_incomplete_absolute() -> None:
    result = assemble(
        make_parts({"mode": ("absolute", 0.9), "month": ("none", 0.9)}),
        TODAY,
    )
    assert result.date is None
    assert "incomplete" in result.note


def test_assemble_unrecognized_mode() -> None:
    result = assemble(make_parts({"mode": ("someday", 0.9)}), TODAY)
    assert result.date is None
    assert "someday" in result.note


def test_resolve_weekday_conventions() -> None:
    assert resolve_weekday(TODAY, "Friday", "none") == date(2026, 10, 2)
    assert resolve_weekday(TODAY, "Thursday", "none") == TODAY
    assert resolve_weekday(TODAY, "Thursday", "next") == date(2026, 10, 8)
    assert resolve_weekday(TODAY, "Thursday", "current") == TODAY
    assert resolve_weekday(TODAY, "Monday", "current") == date(2026, 9, 28)


def test_year_window_current_to_plus_two() -> None:
    specs = build_date_question_specs(TODAY)
    assert set(specs) == {
        "mode",
        "month",
        "day",
        "year",
        "day_anchor",
        "weekday",
        "week_offset",
    }
    assert set(specs["year"].criteria) == {
        "2026",
        "2027",
        "2028",
        "none",
        "out_of_range",
    }


def test_parts_from_answers_missing_degrades() -> None:
    from t.classify.jev import parts_from_answers

    parts = parts_from_answers(
        {
            "mode": ChoiceAnswer(choice="relative", confidence=0.9),
            "weekday": NoulAnswer(noul=0.5),  # wrong type on purpose
        }
    )
    assert parts["mode"] == DatePart(choice="relative", confidence=0.9)
    assert parts["month"] == DatePart(choice="none", confidence=0.0)
    assert parts["weekday"] == DatePart(choice="none", confidence=0.0)
    assert assemble(parts, TODAY).date is None


# -- fake Jev client ------------------------------------------------------


class FakeJevClassifier(JevClassifier):
    """Test double: records requests, replays canned payloads, no network."""

    def __init__(self, payloads: list[dict[str, object]]) -> None:
        super().__init__(api_key="fake", backoff_base=0.0)
        self._payloads = payloads
        self.calls: list[tuple[str, dict[str, ChoiceQuestion | NoulQuestion]]] = []
        self.seen_keys: list[str | None] = []

    def _evaluate(self, state: str, questions: dict[str, ChoiceQuestion | NoulQuestion]) -> object:
        self.calls.append((state, questions))
        return self._payloads[len(self.calls) - 1]


class FakeJevFactory:
    """Callable replacing JevClassifier; hands out one shared fake."""

    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.instance = FakeJevClassifier(payloads)

    def __call__(self, api_key: str | None = None, **kwargs: object) -> FakeJevClassifier:
        self.instance.seen_keys.append(api_key)
        return self.instance


def choice_answer(choice: str, confidence: float) -> dict[str, object]:
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": {choice: 1.0},
        "confidence": confidence,
    }


def date_answers(parts: dict[str, tuple[str, float]]) -> dict[str, dict[str, object]]:
    return {name: choice_answer(choice, conf) for name, (choice, conf) in parts.items()}


def jev_payload(extra_answers: dict[str, dict[str, object]]) -> dict[str, object]:
    payload = jev_response()
    assert isinstance(payload["answers"], dict)
    payload["answers"].update(extra_answers)
    return payload


TOMORROW_PARTS = {
    "mode": ("relative", 0.9),
    "month": ("none", 0.9),
    "day": ("none", 0.9),
    "year": ("none", 0.9),
    "day_anchor": ("tomorrow", 0.85),
    "weekday": ("none", 0.9),
    "week_offset": ("none", 0.9),
}


def test_decide_with_dates_single_request() -> None:
    from t.classify.schema import build_schema

    fake = FakeJevClassifier([jev_payload(date_answers(TOMORROW_PARTS))])
    schema = build_schema(sample_cache())
    decision, result = fake.decide_with_dates("něco, zítra", schema, TODAY)
    assert len(fake.calls) == 1
    state, questions = fake.calls[0]
    assert state == "něco, zítra"
    assert {"board", "list"} <= set(questions)
    assert {
        "mode",
        "month",
        "day",
        "year",
        "day_anchor",
        "weekday",
        "week_offset",
    } <= set(questions)
    assert (decision.board_id, decision.list_id) == ("b1", "l1")
    assert result.date == date(2026, 10, 2)
    assert result.confidence == 0.85


def test_extract_date_only_request() -> None:
    fake = FakeJevClassifier(
        [{"model": "jev-1.13.0", "answers": date_answers(TOMORROW_PARTS), "usage": {}}]
    )
    result = fake.extract_date("něco, zítra", TODAY)
    assert len(fake.calls) == 1
    _, questions = fake.calls[0]
    assert set(questions) == {
        "mode",
        "month",
        "day",
        "year",
        "day_anchor",
        "weekday",
        "week_offset",
    }
    assert result.date == date(2026, 10, 2)


# -- date gate ------------------------------------------------------------


def test_apply_date_gate() -> None:
    ok = JevDateResult(date=date(2026, 10, 2), confidence=0.85, note="", mode="relative", parts={})
    due, warning = apply_date_gate(ok, 0.6)
    assert due == datetime(2026, 10, 2, tzinfo=PRAGUE)
    assert warning is None

    clean_none = JevDateResult(
        date=None, confidence=0.93, note="no such date stated", mode="none", parts={}
    )
    assert apply_date_gate(clean_none, 0.6) == (None, None)  # silent, like parser

    low = JevDateResult(date=date(2026, 10, 2), confidence=0.4, note="", mode="relative", parts={})
    assert apply_date_gate(low, 0.6) == (
        None,
        "Jev date confidence 0.40 is below the 0.60 threshold; no due date.",
    )

    broken = JevDateResult(
        date=None,
        confidence=0.9,
        note="impossible date: February 30",
        mode="absolute",
        parts={},
    )
    due, warning = apply_date_gate(broken, 0.6)
    assert due is None
    assert warning is not None and "February 30" in warning


# -- CLI wiring -----------------------------------------------------------


def set_trello_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "tok")


@respx.mock
def test_add_default_engine_is_jev(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_trello_secrets(monkeypatch)
    monkeypatch.setenv("JEV_API_KEY", "secret")
    monkeypatch.setenv("T_CACHE_PATH", str(tmp_path))
    monkeypatch.setenv("T_LOG_PATH", str(tmp_path))
    mock_trello_for_sample_cache()
    respx.post("https://api.trello.com/1/cards").mock(
        return_value=httpx.Response(
            200,
            json={"id": "c1", "name": "x", "idList": "l1", "url": "https://t/c1"},
        )
    )
    factory = FakeJevFactory(
        [{"model": "jev-1.13.0", "answers": date_answers(TOMORROW_PARTS), "usage": {}}]
    )
    monkeypatch.setattr("t.core.JevClassifier", factory)
    result = runner.invoke(
        app, ["add", "něco, zítra", "--yes", "--cache-path", str(tmp_path / "s.json")]
    )
    assert result.exit_code == 0, result.output
    expected = (datetime.now(PRAGUE).date() + timedelta(days=1)).isoformat()
    assert expected in result.output  # due date came from the Jev engine
    instance: FakeJevClassifier = factory.instance
    assert len(instance.calls) == 1  # mock classifier: one date-only request
    entries = read_last(tmp_path / "decision_log.jsonl", 10)
    assert len(entries) == 1
    assert entries[0].date_engine == "jev"


@respx.mock
def test_add_jev_classifier_single_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_trello_secrets(monkeypatch)
    monkeypatch.setenv("JEV_API_KEY", "secret")
    mock_trello_for_sample_cache()
    factory = FakeJevFactory([jev_payload(date_answers(TOMORROW_PARTS))])
    monkeypatch.setattr("t.core.JevClassifier", factory)
    result = runner.invoke(
        app,
        [
            "add",
            "domluvit konzultaci k projektu, do pátku",
            "--dry-run",
            "--classifier",
            "jev",
            "--cache-path",
            str(tmp_path / "structure.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Work → Todo" in result.output
    instance: FakeJevClassifier = factory.instance
    assert len(instance.calls) == 1  # classification + dates in one request
    _, questions = instance.calls[0]
    assert {"board", "list", "mode", "weekday"} <= set(questions)


@respx.mock
def test_add_jev_date_low_confidence_keeps_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_trello_secrets(monkeypatch)
    monkeypatch.setenv("JEV_API_KEY", "secret")
    mock_trello_for_sample_cache()
    low = dict(TOMORROW_PARTS)
    low["mode"] = ("relative", 0.4)
    factory = FakeJevFactory([{"model": "jev-1.13.0", "answers": date_answers(low), "usage": {}}])
    monkeypatch.setattr("t.core.JevClassifier", factory)
    result = runner.invoke(
        app,
        [
            "add",
            "napsat report do todo",
            "--dry-run",
            "--cache-path",
            str(tmp_path / "structure.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "below" in result.output  # date dropped with a reason…
    assert "Work → Todo" in result.output  # …but the list is untouched


@respx.mock
def test_add_jev_date_impossible_warns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_trello_secrets(monkeypatch)
    monkeypatch.setenv("JEV_API_KEY", "secret")
    mock_trello_for_sample_cache()
    feb30 = {
        "mode": ("absolute", 0.9),
        "month": ("February", 0.9),
        "day": ("30", 0.9),
        "year": ("none", 0.9),
        "day_anchor": ("none", 0.9),
        "weekday": ("none", 0.9),
        "week_offset": ("none", 0.9),
    }
    factory = FakeJevFactory([{"model": "jev-1.13.0", "answers": date_answers(feb30), "usage": {}}])
    monkeypatch.setattr("t.core.JevClassifier", factory)
    result = runner.invoke(
        app,
        ["add", "něco", "--dry-run", "--cache-path", str(tmp_path / "s.json")],
    )
    assert result.exit_code == 0, result.output
    assert "February 30" in result.output
    assert "Due date" in result.output and "(none)" in result.output


@respx.mock
def test_add_explain_shows_jev_engine_and_parts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_trello_secrets(monkeypatch)
    monkeypatch.setenv("JEV_API_KEY", "secret")
    mock_trello_for_sample_cache()
    factory = FakeJevFactory(
        [{"model": "jev-1.13.0", "answers": date_answers(TOMORROW_PARTS), "usage": {}}]
    )
    monkeypatch.setattr("t.core.JevClassifier", factory)
    result = runner.invoke(
        app,
        [
            "add",
            "něco, zítra",
            "--dry-run",
            "--explain",
            "--cache-path",
            str(tmp_path / "s.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "jev" in result.output
    assert "mode=relative" in result.output
    assert "day_anchor=tomorrow" in result.output


@respx.mock
def test_add_explain_parser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_trello_secrets(monkeypatch)
    mock_trello_for_sample_cache()
    result = runner.invoke(
        app,
        [
            "add",
            "něco, do pátku",
            "--dry-run",
            "--explain",
            "--date-engine",
            "parser",
            "--cache-path",
            str(tmp_path / "s.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "parser" in result.output
    assert "do pátku" in result.output


@respx.mock
def test_add_date_engine_parser_uses_old_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_trello_secrets(monkeypatch)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setenv("T_CACHE_PATH", str(tmp_path))
    monkeypatch.setenv("T_LOG_PATH", str(tmp_path))
    mock_trello_for_sample_cache()
    respx.post("https://api.trello.com/1/cards").mock(
        return_value=httpx.Response(200, json={"id": "c1", "name": "x", "idList": "l1"})
    )
    factory = FakeJevFactory([])
    monkeypatch.setattr("t.core.JevClassifier", factory)
    result = runner.invoke(
        app,
        [
            "add",
            "domluvit konzultaci k projektu, do pátku",
            "--yes",
            "--date-engine",
            "parser",
            "--cache-path",
            str(tmp_path / "s.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    instance: FakeJevClassifier = factory.instance
    assert instance.calls == []  # parser path never touches Jev
    entries = read_last(tmp_path / "decision_log.jsonl", 10)
    assert entries[0].date_engine == "parser"


@respx.mock
def test_add_date_engine_from_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_trello_secrets(monkeypatch)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    mock_trello_for_sample_cache()
    config = tmp_path / "config.toml"
    config_mod.save_settings(config_mod.Settings(date_engine="parser"), config)
    result = runner.invoke(
        app,
        [
            "add",
            "něco, do pátku",
            "--dry-run",
            "--config-path",
            str(config),
            "--cache-path",
            str(tmp_path / "s.json"),
        ],
    )
    assert result.exit_code == 0, result.output  # no Jev key needed


def test_add_date_engine_invalid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_trello_secrets(monkeypatch)
    result = runner.invoke(
        app,
        [
            "add",
            "něco",
            "--dry-run",
            "--date-engine",
            "bogus",
            "--cache-path",
            str(tmp_path / "s.json"),
        ],
    )
    assert result.exit_code == 1
    assert "Unknown date engine" in result.output


@respx.mock
def test_add_jev_engine_without_key_no_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_trello_secrets(monkeypatch)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    mock_trello_for_sample_cache()
    result = runner.invoke(
        app,
        ["add", "něco, zítra", "--dry-run", "--cache-path", str(tmp_path / "s.json")],
    )
    assert result.exit_code == 1  # no silent fallback to parser
    assert "JEV_API_KEY" in result.output


def test_log_entry_date_engine_default(tmp_path: Path) -> None:
    path = tmp_path / "decision_log.jsonl"
    # A pre-feature line has no date_engine key and must still parse as parser.
    path.write_text(
        '{"ts":"2026-10-01T10:00:00+02:00","input_text":"x",'
        '"decision":{"board_id":"b","list_id":"l","label_ids":[],"title":"t",'
        '"description":null,"due_phrase":null,"confidence":0.5},'
        '"card_id":null,"card_url":null}\n',
        encoding="utf-8",
    )
    entries = read_last(path, 10)
    assert len(entries) == 1
    assert entries[0].date_engine == "parser"
