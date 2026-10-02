"""M1 tests: ``t sync`` and ``t doctor`` end to end with mocked HTTP."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from t.cli import app

runner = CliRunner()


def set_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "tok")


def mock_structure() -> None:
    respx.get("https://api.trello.com/1/members/me/boards").mock(
        return_value=httpx.Response(200, json=[{"id": "b1", "name": "Work"}])
    )
    respx.get("https://api.trello.com/1/boards/b1/lists").mock(
        return_value=httpx.Response(200, json=[{"id": "l1", "name": "Todo", "idBoard": "b1"}])
    )
    respx.get("https://api.trello.com/1/boards/b1/labels").mock(
        return_value=httpx.Response(200, json=[])
    )


@respx.mock
def test_sync_writes_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_secrets(monkeypatch)
    mock_structure()
    cache = tmp_path / "structure.json"
    result = runner.invoke(app, ["sync", "--cache-path", str(cache)])
    assert result.exit_code == 0, result.output
    assert "1 board(s)" in result.output
    data = json.loads(cache.read_text(encoding="utf-8"))
    assert data["boards"][0]["board"]["name"] == "Work"


@respx.mock
def test_sync_401_exits_nonzero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRELLO_KEY", "bad")
    monkeypatch.setenv("TRELLO_TOKEN", "tok")
    respx.get("https://api.trello.com/1/members/me/boards").mock(
        return_value=httpx.Response(401, json="invalid key")
    )
    result = runner.invoke(app, ["sync", "--cache-path", str(tmp_path / "structure.json")])
    assert result.exit_code == 1
    assert "bad" not in result.output  # no secret material echoed


@respx.mock
def test_doctor_all_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_secrets(monkeypatch)
    respx.get("https://api.trello.com/1/members/me").mock(
        return_value=httpx.Response(200, json={"id": "m1", "username": "tester", "fullName": "T"})
    )
    mock_structure()
    cache = tmp_path / "structure.json"
    config = tmp_path / "config.toml"
    assert runner.invoke(app, ["sync", "--cache-path", str(cache)]).exit_code == 0
    result = runner.invoke(
        app, ["doctor", "--config-path", str(config), "--cache-path", str(cache)]
    )
    assert result.exit_code == 0, result.output
    assert "tester" in result.output


def test_doctor_missing_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRELLO_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)
    result = runner.invoke(app, ["doctor", "--cache-path", str(tmp_path / "structure.json")])
    assert result.exit_code == 1
    assert "TRELLO_KEY" in result.output
