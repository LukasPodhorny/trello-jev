"""M5 tests: ``t init`` interactive flow and board selection parsing."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from t import config as config_mod
from t.cli import app, parse_board_choice
from t.trello.models import Board

runner = CliRunner()
BOARDS = [Board(id="b1", name="Work"), Board(id="b2", name="Home")]


def test_parse_board_choice() -> None:
    assert parse_board_choice("", BOARDS) == BOARDS
    assert parse_board_choice("2", BOARDS) == [BOARDS[1]]
    assert parse_board_choice("1, 2", BOARDS) == BOARDS
    assert parse_board_choice("0", BOARDS) is None
    assert parse_board_choice("3", BOARDS) is None
    assert parse_board_choice("x", BOARDS) is None
    assert parse_board_choice("1,1", BOARDS) is None


def mock_init_http() -> None:
    respx.get("https://api.trello.com/1/members/me").mock(
        return_value=httpx.Response(200, json={"id": "m1", "username": "tester", "fullName": "T"})
    )
    respx.get("https://api.trello.com/1/members/me/boards").mock(
        return_value=httpx.Response(
            200, json=[{"id": "b1", "name": "Work"}, {"id": "b2", "name": "Home"}]
        )
    )
    respx.get("https://api.trello.com/1/boards/b1/lists").mock(
        return_value=httpx.Response(200, json=[{"id": "l1", "name": "Todo", "idBoard": "b1"}])
    )
    respx.get("https://api.trello.com/1/boards/b1/labels").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post("https://api.trello.com/1/lists").mock(
        return_value=httpx.Response(200, json={"id": "l9", "name": "Inbox", "idBoard": "b1"})
    )
    respx.post("https://api.trello.com/1/labels").mock(
        return_value=httpx.Response(
            200, json={"id": "lb9", "name": "k roztřídění", "color": "yellow"}
        )
    )


@respx.mock
def test_init_full_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    mock_init_http()
    config = tmp_path / "config.toml"
    # key, token, board choice "1", create list yes, create label yes, .env no
    result = runner.invoke(
        app,
        ["init", "--config-path", str(config)],
        input="mykey\nmytoken\n1\ny\ny\nn\n",
    )
    assert result.exit_code == 0, result.output
    assert "Connected" in result.output
    assert "Created list" in result.output
    assert "Created label" in result.output
    settings = config_mod.load_settings(config)
    assert settings.board_ids == ["b1"]
    assert not (tmp_path / ".env").exists()


@respx.mock
def test_init_bad_credentials(tmp_path: Path) -> None:
    respx.get("https://api.trello.com/1/members/me").mock(
        return_value=httpx.Response(401, json="invalid key")
    )
    result = runner.invoke(
        app,
        ["init", "--config-path", str(tmp_path / "config.toml")],
        input="bad\nbad\n",
    )
    assert result.exit_code == 1
    # The typed key is echoed by the terminal harness (normal stdin echo);
    # what matters is that error output never carries secret material.
    error_lines = [line for line in result.output.splitlines() if "Error" in line]
    assert error_lines
    assert all("bad" not in line for line in error_lines)


@respx.mock
def test_create_list_and_label() -> None:
    post_list = respx.post("https://api.trello.com/1/lists").mock(
        return_value=httpx.Response(200, json={"id": "l9", "name": "Inbox", "idBoard": "b1"})
    )
    post_label = respx.post("https://api.trello.com/1/labels").mock(
        return_value=httpx.Response(
            200, json={"id": "lb9", "name": "k roztřídění", "color": "yellow"}
        )
    )
    from t.trello.client import TrelloClient

    with TrelloClient("k", "tok", backoff_base=0.0) as client:
        lst = client.create_list("b1", "Inbox")
        label = client.create_label("b1", "k roztřídění", "yellow")
    assert lst.id == "l9"
    assert label.color == "yellow"
    assert post_list.calls[0].request.url.params["idBoard"] == "b1"
    assert post_label.calls[0].request.url.params["color"] == "yellow"
