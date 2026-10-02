# t — file a task into the right Trello list with one sentence

```bash
t add "domluvit konzultaci k projektu, do pátku"
```

`t` reads your Trello structure, lets a classifier decide (board → list,
labels) from real names/IDs, deterministically resolves the due date, and
creates the card via the Trello REST API. On low confidence the card lands
in the **Inbox** list with the `k roztřídění` label.

## Installation

```bash
uv sync
```

Dev commands: `scripts/ci.sh` (ruff + mypy --strict + pytest).

## First run

Details in [SETUP.md](SETUP.md) (getting the API key and token; in Czech).

```bash
t init                              # wizard: login, boards, Inbox/label
t sync                              # download structure into the cache
t add "úkol, do pátku" --dry-run    # show the decision only, create nothing
t add "úkol, do pátku"              # preview + confirm + create the card
t doctor                            # check config, token and cache
t log                               # last N decisions
```

Useful `t add` flags: `--yes` (skip confirmation), `--board <name>`
(restrict the schema to one board), `--classifier mock|jev`.

## How it works

1. `t sync` stores boards, lists and labels in a local cache (TTL 1 h).
2. The cache builds a typed schema (enums = real names/IDs).
3. The classifier (`mock` – keyword rules; `jev` – the Jev model via the
   TypeSafe System One API: one fan-out query with a Choice on board, a
   Choice on list and a Noul per label) returns a `Decision`; the output
   is validated against the schema, an invalid ID is an error, never a
   silent fix.
4. `due_phrase` (the raw date text) is converted to a date by `dates.py`
   (cs + en, Europe/Prague). The model never computes dates.
5. The card is created via `POST /1/cards`; the decision is written to
   `decision_log.jsonl` for later accuracy measurement.

## Configuration

- Secrets: `TRELLO_KEY`, `TRELLO_TOKEN` (optionally `JEV_API_KEY`) in the
  environment or `.env`. Never into git, never into logs.
- Settings: `~/.config/t/config.toml` (`threshold` 0.6, Inbox names, cache
  TTL, selected boards). Paths can be overridden via `T_CONFIG_PATH`,
  `T_CACHE_PATH`, `T_LOG_PATH`.

## MCP server (for AI agents)

`t mcp` exposes the same pipeline as `t add` through four tools:
`add_task` (creates the card, no confirmation; low confidence routes to
the Inbox), `preview_task` (dry run), `list_targets` (boards/lists/labels),
`list_inbox` (cards awaiting triage). The server can **only create
cards** – no deleting, moving or editing.

> Note: the server runs with your Trello token and `add_task` really
> creates cards in your workspace. Calls are throttled
> (`mcp_add_per_hour`, default 30/h) and tagged `source="mcp"` in the log.

The server reads secrets from the same environment/config as the CLI; if
you saved `.env` in the repo during `t init`, nothing else is needed.
Otherwise pass them via `env`/`--env` (below).

**Claude Code** (syntax verified against the official docs):

```bash
claude mcp add --transport stdio t -- /path/to/trello-jev/.venv/bin/t mcp
```

with secrets passed explicitly:

```bash
claude mcp add --transport stdio t \
  --env TRELLO_KEY=... --env TRELLO_TOKEN=... --env JEV_API_KEY=... \
  -- /path/to/trello-jev/.venv/bin/t mcp
```

**Claude Desktop**: into `claude_desktop_config.json` (`~/Library/Application
Support/Claude/` on macOS, `%APPDATA%\Claude\` on Windows; then restart
the app):

```json
{
  "mcpServers": {
    "t": {
      "command": "/path/to/trello-jev/.venv/bin/t",
      "args": ["mcp"]
    }
  }
}
```

**Antigravity**: stdio servers are registered via the config file
`~/.gemini/config/mcp_config.json` (globally; `.agents/mcp_config.json`
applies per workspace):

```json
{
  "mcpServers": {
    "t": {
      "command": "/path/to/trello-jev/.venv/bin/t",
      "args": ["mcp"]
    }
  }
}
```

(The `agy mcp add --url ...` command is reported for remote HTTP servers
only and I could not verify it against official docs.)

**HTTP mode** (instead of stdio): `t mcp --http --port 8000` listens on
`127.0.0.1:8000/mcp`. Binding a non-local address (`--host 0.0.0.0`)
requires `T_MCP_TOKEN` to be set, otherwise the server refuses to start.
