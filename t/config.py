"""Configuration and secrets.

Secrets live in env vars (``TRELLO_KEY``, ``TRELLO_TOKEN``, ``JEV_API_KEY``),
optionally loaded from a ``.env`` file. They are never printed or logged.

Non-identifying settings live in ``~/.config/t/config.toml``.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import tomli_w
from dotenv import load_dotenv
from pydantic import BaseModel

DEFAULT_THRESHOLD = 0.6
DEFAULT_INBOX_LIST_NAME = "Inbox"
DEFAULT_NEEDS_SORTING_LABEL = "k roztřídění"
DEFAULT_CACHE_TTL_SECONDS = 3600
DEFAULT_MCP_ADD_PER_HOUR = 30


class ConfigError(Exception):
    """Config or secrets are missing/invalid (message never contains secrets)."""


class Settings(BaseModel):
    threshold: float = DEFAULT_THRESHOLD
    inbox_list_name: str = DEFAULT_INBOX_LIST_NAME
    needs_sorting_label: str = DEFAULT_NEEDS_SORTING_LABEL
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    board_ids: list[str] = []
    jev_model: str = "jev-latest"
    date_engine: str = "jev"
    mcp_add_per_hour: int = DEFAULT_MCP_ADD_PER_HOUR


class Secrets(BaseModel):
    trello_key: str
    trello_token: str
    jev_api_key: str | None = None


def default_config_path() -> Path:
    return Path(os.environ.get("T_CONFIG_PATH", Path.home() / ".config" / "t")) / ("config.toml")


def default_cache_path() -> Path:
    return Path(os.environ.get("T_CACHE_PATH", Path.home() / ".cache" / "t")) / ("structure.json")


def default_log_path() -> Path:
    return Path(os.environ.get("T_LOG_PATH", Path.home() / ".cache" / "t")) / "decision_log.jsonl"


def load_settings(path: Path | None = None) -> Settings:
    """Load settings; missing file means defaults."""
    cfg = path or default_config_path()
    if not cfg.exists():
        return Settings()
    try:
        with cfg.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"Cannot read config file {cfg}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {cfg} must contain a TOML table.")
    return Settings.model_validate(data)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """Write settings to disk (used by ``t init``)."""
    cfg = path or default_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(tomli_w.dumps(settings.model_dump()), encoding="utf-8")
    return cfg


def load_secrets(env_file: Path | None = None) -> Secrets:
    """Load secrets from the environment (plus an optional ``.env`` file)."""
    load_dotenv(dotenv_path=env_file, verbose=False)
    key = os.environ.get("TRELLO_KEY", "")
    token = os.environ.get("TRELLO_TOKEN", "")
    if not key or not token:
        missing = ", ".join(
            name for name, value in (("TRELLO_KEY", key), ("TRELLO_TOKEN", token)) if not value
        )
        raise ConfigError(
            f"Missing secrets: {missing}. Set them in the environment or a .env file; see SETUP.md."
        )
    return Secrets(trello_key=key, trello_token=token, jev_api_key=os.environ.get("JEV_API_KEY"))
