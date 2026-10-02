"""Isolate CLI tests from ambient state: real ~/.config/ ~/.cache, shell
secrets, and the repo-root .env (load_dotenv resolves it from t/config.py's
location no matter the cwd, so tests neutralize it and set env explicitly)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_t_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T_CONFIG_PATH", str(tmp_path / "home-config"))
    monkeypatch.setenv("T_CACHE_PATH", str(tmp_path / "home-cache"))
    monkeypatch.setenv("T_LOG_PATH", str(tmp_path / "home-logs"))
    for var in ("TRELLO_KEY", "TRELLO_TOKEN", "JEV_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("t.config.load_dotenv", lambda *args, **kwargs: False, raising=True)
