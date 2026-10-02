"""M1 tests: config defaults/file, secrets handling, cache round-trip."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from t import config as config_mod
from t.config import ConfigError
from t.trello import cache as cache_mod
from t.trello.cache import BoardStructure, CachedStructure
from t.trello.models import Board, Label, TrelloList


def sample_structure() -> CachedStructure:
    return CachedStructure(
        fetched_at=cache_mod.utcnow(),
        boards=[
            BoardStructure(
                board=Board(id="b1", name="Work"),
                lists=[TrelloList(id="l1", name="Todo", idBoard="b1")],
                labels=[Label(id="lb1", name="urgent", color="red")],
            )
        ],
    )


def test_settings_defaults_when_no_file(tmp_path: Path) -> None:
    settings = config_mod.load_settings(tmp_path / "missing.toml")
    assert settings.threshold == 0.6
    assert settings.inbox_list_name == "Inbox"
    assert settings.cache_ttl_seconds == 3600
    assert settings.board_ids == []


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    saved = config_mod.save_settings(config_mod.Settings(threshold=0.8, board_ids=["b1"]), path)
    assert saved == path
    loaded = config_mod.load_settings(path)
    assert loaded.threshold == 0.8
    assert loaded.board_ids == ["b1"]


def test_settings_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("threshold = [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError):
        config_mod.load_settings(path)


def test_secrets_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "tok")
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    secrets = config_mod.load_secrets(tmp_path / ".env-missing")
    assert secrets.trello_key == "k"
    assert secrets.jev_api_key is None


def test_secrets_missing_raises_no_values_leaked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TRELLO_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)
    with pytest.raises(ConfigError) as exc:
        config_mod.load_secrets(tmp_path / ".env-missing")
    assert "TRELLO_KEY" in str(exc.value)


def test_cache_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "structure.json"
    cache_mod.save_cache(sample_structure(), path)
    loaded = cache_mod.load_cache(path)
    assert loaded is not None
    assert loaded.boards[0].board.name == "Work"
    assert loaded.boards[0].lists[0].name == "Todo"
    assert loaded.boards[0].labels[0].name == "urgent"


def test_cache_missing_is_none(tmp_path: Path) -> None:
    assert cache_mod.load_cache(tmp_path / "nope.json") is None


def test_cache_corrupt_is_none(tmp_path: Path) -> None:
    path = tmp_path / "structure.json"
    path.write_text("{not json", encoding="utf-8")
    assert cache_mod.load_cache(path) is None


def test_cache_freshness() -> None:
    fresh = sample_structure()
    assert cache_mod.is_fresh(fresh, 3600) is True
    stale = CachedStructure(fetched_at=fresh.fetched_at - timedelta(hours=2), boards=[])
    assert cache_mod.is_fresh(stale, 3600) is False
