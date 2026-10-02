"""MCP server tests: tool units with fakes, stdio smoke via SDK client."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from t import config as config_mod
from t import mcp_server as mcp_server_mod
from t.cli import app
from t.decision_log import read_last
from t.mcp_server import (
    McpStartupError,
    McpToolError,
    add_task,
    card_created_iso,
    check_http_token,
    list_inbox,
    list_targets,
    preview_task,
    reset_add_rate_limit,
)
from t.trello import cache as cache_mod
from tests.test_classify_add import sample_cache
from tests.test_jev_dates import TOMORROW_PARTS, FakeJevFactory, date_answers

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_add_rate_limit()


def set_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "tok")
    monkeypatch.setenv("JEV_API_KEY", "secret")


def mock_trello_boards() -> None:
    respx.get("https://api.trello.com/1/members/me/boards").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "b1", "name": "Work"},
                {"id": "b2", "name": "Home"},
            ],
        )
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
    respx.get("https://api.trello.com/1/boards/b2/lists").mock(
        return_value=httpx.Response(200, json=[{"id": "l3", "name": "Todo", "idBoard": "b2"}])
    )
    respx.get("https://api.trello.com/1/boards/b2/labels").mock(
        return_value=httpx.Response(200, json=[])
    )


def mock_card_create() -> respx.Route:
    return respx.post("https://api.trello.com/1/cards").mock(
        return_value=httpx.Response(
            200,
            json={"id": "c1", "name": "x", "idList": "l1", "url": "https://t/c1"},
        )
    )


def fake_jev_dates(monkeypatch: pytest.MonkeyPatch) -> FakeJevFactory:
    factory = FakeJevFactory(
        [{"model": "jev-1.13.0", "answers": date_answers(TOMORROW_PARTS), "usage": {}}]
    )
    monkeypatch.setattr("t.core.JevClassifier", factory)
    return factory


@respx.mock
def test_add_task_creates_exactly_one_card(monkeypatch: pytest.MonkeyPatch) -> None:
    set_secrets(monkeypatch)
    mock_trello_boards()
    post = mock_card_create()
    factory = fake_jev_dates(monkeypatch)
    result = add_task("něco, zítra")
    assert result.card_url == "https://t/c1"
    assert result.card_id == "c1"
    assert len(post.calls) == 1
    assert len(factory.instance.calls) == 1  # one date-only Jev request
    entries = read_last(config_mod.default_log_path(), 10)
    assert len(entries) == 1
    assert entries[0].source == "mcp"
    assert entries[0].date_engine == "jev"


@respx.mock
def test_preview_task_creates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    set_secrets(monkeypatch)
    mock_trello_boards()
    post = mock_card_create()
    fake_jev_dates(monkeypatch)
    result = preview_task("něco, zítra")
    assert result.card_url is None
    assert result.card_id is None
    assert result.board_name == "Work"
    assert len(post.calls) == 0


@respx.mock
def test_list_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    set_secrets(monkeypatch)
    mock_trello_boards()
    boards = list_targets()
    assert [(b.id, b.name) for b in boards] == [("b1", "Work"), ("b2", "Home")]
    assert [lst.name for lst in boards[0].lists] == ["Todo", "Inbox"]
    assert [(label.name, label.color) for label in boards[0].labels] == [
        ("urgent", "red"),
        ("k roztřídění", "yellow"),
    ]


@respx.mock
def test_list_inbox_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    set_secrets(monkeypatch)
    cache_mod.save_cache(sample_cache(), config_mod.default_cache_path())
    respx.get("https://api.trello.com/1/lists/l2/cards").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "00000000aaaabbbbccccdddd",
                    "name": "old",
                    "url": "https://t/old",
                    "idList": "l2",
                },
                {
                    "id": "68f3fe9440f116a7a7572ce5",
                    "name": "new",
                    "url": "https://t/new",
                    "idList": "l2",
                },
            ],
        )
    )
    cards = list_inbox()
    assert [card.title for card in cards] == ["new", "old"]
    assert cards[0].created == "2025-10-18T20:54:44+00:00"
    assert cards[0].board == "Work"
    assert cards[0].url == "https://t/new"


def test_list_inbox_limit_bounds() -> None:
    with pytest.raises(McpToolError, match="between 1 and 100"):
        list_inbox(limit=0)
    with pytest.raises(McpToolError, match="between 1 and 100"):
        list_inbox(limit=101)


def test_validate_text() -> None:
    with pytest.raises(McpToolError, match="must not be empty"):
        add_task("   ")
    with pytest.raises(McpToolError, match="too long"):
        preview_task("x" * 501)


def test_card_created_iso() -> None:
    assert card_created_iso("68f3fe9440f116a7a7572ce5") == "2025-10-18T20:54:44+00:00"
    assert card_created_iso("00000000aaaabbbbccccdddd") == "1970-01-01T00:00:00+00:00"
    assert card_created_iso("zzz") is None
    assert card_created_iso("short") is None


@respx.mock
def test_add_task_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    set_secrets(monkeypatch)
    config_mod.save_settings(
        config_mod.Settings(mcp_add_per_hour=1), config_mod.default_config_path()
    )
    mock_trello_boards()
    mock_card_create()
    factory = FakeJevFactory(
        [
            {"model": "m", "answers": date_answers(TOMORROW_PARTS), "usage": {}},
            {"model": "m", "answers": date_answers(TOMORROW_PARTS), "usage": {}},
        ]
    )
    monkeypatch.setattr("t.core.JevClassifier", factory)
    add_task("první")
    with pytest.raises(McpToolError, match="rate limit exceeded \\(1/hour\\)"):
        add_task("druhý")


@respx.mock
def test_add_task_trello_auth_error_is_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secrets(monkeypatch)
    respx.get("https://api.trello.com/1/members/me/boards").mock(
        return_value=httpx.Response(401, json="invalid key")
    )
    with pytest.raises(McpToolError) as exc:
        add_task("něco")
    assert "Trello" in str(exc.value)
    assert "Traceback" not in str(exc.value)


def test_check_http_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("T_MCP_TOKEN", raising=False)
    check_http_token("127.0.0.1")
    check_http_token("localhost")
    check_http_token("::1")
    with pytest.raises(McpStartupError, match="T_MCP_TOKEN"):
        check_http_token("0.0.0.0")
    monkeypatch.setenv("T_MCP_TOKEN", "tok")
    check_http_token("0.0.0.0")


def test_mcp_http_token_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("T_MCP_TOKEN", raising=False)
    result = runner.invoke(app, ["mcp", "--http", "--host", "0.0.0.0", "--port", "8123"])
    assert result.exit_code == 1
    assert "T_MCP_TOKEN" in result.output


def _smoke_env(tmp_path: Path) -> dict[str, str]:
    cfg_dir = tmp_path / "cfg"
    cache_dir = tmp_path / "cache"
    log_dir = tmp_path / "logs"
    cfg_dir.mkdir()
    config_mod.save_settings(config_mod.Settings(date_engine="parser"), cfg_dir / "config.toml")
    cache_mod.save_cache(sample_cache(), cache_dir / "structure.json")
    env = dict(os.environ)
    env.update(
        {
            "TRELLO_KEY": "k",
            "TRELLO_TOKEN": "tok",
            "JEV_API_KEY": "bogus",
            "T_CONFIG_PATH": str(cfg_dir),
            "T_CACHE_PATH": str(cache_dir),
            "T_LOG_PATH": str(log_dir),
            # Hermeticity fuse: any accidental real network fails fast, never hangs.
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
        }
    )
    return env


def test_mcp_stdio_smoke(tmp_path: Path) -> None:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def run() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "t.cli", "mcp"],
            env=_smoke_env(tmp_path),
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "add_task",
                    "preview_task",
                    "list_targets",
                    "list_inbox",
                }
                by_name = {tool.name: tool for tool in tools.tools}
                assert "exactly as written" in (by_name["add_task"].description or "")
                assert "exactly as written" in (by_name["preview_task"].description or "")
                assert by_name["preview_task"].annotations is not None
                assert by_name["preview_task"].annotations.read_only_hint is True
                assert by_name["add_task"].annotations is not None
                assert by_name["add_task"].annotations.read_only_hint is False
                assert by_name["add_task"].annotations.destructive_hint is False
                assert by_name["add_task"].annotations.idempotent_hint is False
                result = await session.call_tool("preview_task", {"text": "uklidit stůl, zítra"})
                assert not result.is_error, result.content
                payload = result.structured_content or json.loads(
                    result.content[0].text  # type: ignore[union-attr]
                )
                assert payload["card_url"] is None
                assert "uklidit" in payload["decision"]["title"]

    asyncio.run(run())


def test_tool_functions_are_plain_callables() -> None:
    # The @server.tool decorator returns the original function, so units
    # above exercise exactly what the server exposes.
    assert callable(add_task) and add_task.__name__ == "add_task"
    assert mcp_server_mod.server is not None
