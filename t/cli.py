"""Typer CLI: sync, doctor, add, init, log."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from t import config as config_mod
from t import core as core_mod
from t.classify.base import Decision
from t.classify.jev import JevClassifier, JevError
from t.classify.jev_dates import JevDateResult
from t.classify.schema import (
    InvalidDecisionError,
    SchemaError,
    StructureSchema,
    build_schema,
)
from t.config import ConfigError
from t.core import apply_date_gate as apply_date_gate
from t.dates import PRAGUE
from t.decision_log import read_last
from t.trello import cache as cache_mod
from t.trello.client import TrelloAuthError, TrelloClient, TrelloError
from t.trello.models import Board

app = typer.Typer(no_args_is_help=True)
console = Console()


def main() -> None:
    app()


@app.command()
def sync(
    config_path: Annotated[Path | None, typer.Option(help="Config file path.")] = None,
    cache_path: Annotated[Path | None, typer.Option(help="Cache file path.")] = None,
) -> None:
    """Re-download the Trello structure into the local cache."""
    try:
        settings = config_mod.load_settings(config_path)
        secrets = config_mod.load_secrets()
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    target = cache_path or config_mod.default_cache_path()
    try:
        cached = core_mod.sync_structure(secrets, settings, target)
    except (TrelloAuthError, TrelloError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    n_lists = sum(len(b.lists) for b in cached.boards)
    n_labels = sum(len(b.labels) for b in cached.boards)
    console.print(
        f"[green]Synced[/green] {len(cached.boards)} board(s), "
        f"{n_lists} list(s), {n_labels} label(s) → {target}"
    )


@app.command()
def doctor(
    config_path: Annotated[Path | None, typer.Option(help="Config file path.")] = None,
    cache_path: Annotated[Path | None, typer.Option(help="Cache file path.")] = None,
) -> None:
    """Verify config, token validity, cache freshness and Jev reachability."""
    table = Table(title="t doctor")
    table.add_column("Check")
    table.add_column("Result")
    ok = True

    cfg_file = config_path or config_mod.default_config_path()
    settings: config_mod.Settings | None = None
    try:
        settings = config_mod.load_settings(config_path)
        table.add_row("Config file", f"OK ({cfg_file})")
    except ConfigError as e:
        ok = False
        table.add_row("Config file", f"FAIL: {e}")

    try:
        secrets = config_mod.load_secrets()
        table.add_row("Secrets (TRELLO_KEY/TRELLO_TOKEN)", "OK (present, not shown)")
    except ConfigError as e:
        ok = False
        table.add_row("Secrets (TRELLO_KEY/TRELLO_TOKEN)", f"FAIL: {e}")
        console.print(table)
        raise typer.Exit(1) from e

    try:
        with TrelloClient(secrets.trello_key, secrets.trello_token) as client:
            me = client.get_me()
        table.add_row("Trello token", f"OK (user: {me.get('username', '?')})")
    except (TrelloAuthError, TrelloError) as e:
        ok = False
        table.add_row("Trello token", f"FAIL: {e}")

    cache_file = cache_path or config_mod.default_cache_path()
    cached = cache_mod.load_cache(cache_file)
    if cached is None:
        ok = False
        table.add_row("Cache", f"FAIL: no cache at {cache_file}; run `t sync`")
    elif settings is not None and cache_mod.is_fresh(cached, settings.cache_ttl_seconds):
        table.add_row("Cache", f"OK ({len(cached.boards)} board(s), fetched {cached.fetched_at})")
    else:
        ok = False
        table.add_row("Cache", f"STALE (fetched {cached.fetched_at}); run `t sync`")

    if secrets.jev_api_key:
        try:
            with JevClassifier(secrets.jev_api_key) as jev:
                jev.ping()
            table.add_row("Jev classifier", "OK (reachable)")
        except (JevError, TrelloError) as e:
            table.add_row("Jev classifier", f"FAIL: {e}")
            ok = False
    else:
        table.add_row("Jev classifier", "TODO: JEV_API_KEY not set (M3)")

    console.print(table)
    if not ok:
        raise typer.Exit(1)


def render_preview(
    decision: Decision, schema: StructureSchema, due: datetime | None = None
) -> Table:
    """Human-readable preview of where the card would land."""
    board = schema.board(decision.board_id)
    list_name = next(
        (lst.name for lst in board.lists if lst.id == decision.list_id), decision.list_id
    )
    label_names = [
        next((lb.name or lb.id for lb in board.labels if lb.id == lid), lid)
        for lid in decision.label_ids
    ]
    table = Table(title="Preview")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Title", decision.title)
    table.add_row("Board → List", f"{board.name} → {list_name}")
    table.add_row("Labels", ", ".join(label_names) or "(none)")
    table.add_row("Due phrase", decision.due_phrase or "(none)")
    table.add_row("Due date", due.strftime("%Y-%m-%d %H:%M %Z") if due else "(none)")
    table.add_row("Confidence", f"{decision.confidence:.2f}")
    return table


@app.command()
def add(
    text: Annotated[str, typer.Argument(help="Task in one sentence.")],
    dry_run: Annotated[bool, typer.Option(help="Only show the decision.")] = False,
    yes: Annotated[bool, typer.Option(help="Skip confirmation.")] = False,
    board: Annotated[
        str | None, typer.Option(help="Restrict the schema to one board name.")
    ] = None,
    classifier: Annotated[str, typer.Option(help="Classifier: mock|jev.")] = "mock",
    date_engine: Annotated[
        str | None,
        typer.Option(help="Date engine: jev|parser (default from config)."),
    ] = None,
    explain: Annotated[bool, typer.Option(help="Show how the due date was found.")] = False,
    config_path: Annotated[Path | None, typer.Option(help="Config file path.")] = None,
    cache_path: Annotated[Path | None, typer.Option(help="Cache file path.")] = None,
) -> None:
    """Classify one task and create the Trello card."""
    try:
        settings = config_mod.load_settings(config_path)
        secrets = config_mod.load_secrets()
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    engine = date_engine or settings.date_engine
    if engine not in ("jev", "parser"):
        console.print(
            f"[red]Error:[/red] Unknown date engine {engine!r} "
            "(use --date-engine jev|parser or set date_engine in config.toml)."
        )
        raise typer.Exit(1)
    if classifier not in ("mock", "jev"):
        console.print(f"[red]Error:[/red] Unknown classifier {classifier!r}.")
        raise typer.Exit(1)
    target = cache_path or config_mod.default_cache_path()
    log_path = config_mod.default_log_path()
    try:
        plan = core_mod.plan_task(
            text,
            board=board,
            classifier=classifier,
            engine=engine,
            settings=settings,
            secrets=secrets,
            cache_path=target,
            today=datetime.now(PRAGUE).date(),
        )
    except (
        TrelloAuthError,
        TrelloError,
        SchemaError,
        InvalidDecisionError,
        JevError,  # also covers JevAuthError and JevRateLimitError
    ) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    if plan.routed_to_inbox:
        console.print(
            f"[yellow]Low confidence[/yellow] ({plan.decision.confidence:.2f} < "
            f"{settings.threshold:.2f}) — routing to "
            f"{plan.board_name} → {plan.list_name} with label {plan.inbox_label_name!r}."
        )
    for warning in plan.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")
    cached = cache_mod.load_cache(target)
    assert cached is not None  # plan_task just ensured the cache file exists
    schema = build_schema(cached, board)
    console.print(render_preview(plan.decision, schema, plan.due))
    if explain:
        console.print(
            render_date_explain(
                plan.date_engine,
                plan.decision.due_phrase,
                plan.due,
                plan.jev_date,
                settings.threshold,
            )
        )
    if dry_run:
        console.print("[yellow]Dry run — no card created.[/yellow]")
        return
    if not yes:
        typer.confirm("Create this card?", abort=True)
    try:
        result = core_mod.finalize_plan(
            plan, input_text=text, source="cli", secrets=secrets, log_path=log_path
        )
    except (TrelloAuthError, TrelloError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    if result.card_url:
        console.print(f"[green]Created:[/green] {result.card_url}")
    else:
        console.print(f"[green]Created card[/green] {result.card_id}.")


def render_date_explain(
    engine: str,
    due_phrase: str | None,
    due: datetime | None,
    jev_date: JevDateResult | None,
    threshold: float,
) -> Table:
    """Show which date engine ran and what it concluded."""
    table = Table(title="Explain (date engine)")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Date engine", engine)
    if engine == "jev" and jev_date is not None:
        resolved = str(jev_date.date) if jev_date.date else f"none ({jev_date.note})"
        table.add_row("Resolved", resolved)
        table.add_row("Confidence", f"{jev_date.confidence:.2f} (threshold {threshold:.2f})")
        parts = ", ".join(
            f"{name}={part.choice} ({part.confidence:.2f})" for name, part in jev_date.parts.items()
        )
        table.add_row("Parts", parts or "(none)")
    else:
        table.add_row("Due phrase", due_phrase or "(none)")
        table.add_row("Parsed", due.isoformat() if due else "(none)")
    return table


def apply_low_confidence_fallback(
    decision: Decision,
    schema: StructureSchema,
    settings: config_mod.Settings,
) -> Decision:
    """Route low-confidence decisions to the Inbox list with a sorting label."""
    route = core_mod.route_to_inbox(decision, schema, settings)
    if route.routed:
        board = schema.board(decision.board_id)
        console.print(
            f"[yellow]Low confidence[/yellow] ({decision.confidence:.2f} < "
            f"{settings.threshold:.2f}) — routing to "
            f"{board.name} → {route.inbox_name} with label {route.label_name!r}."
        )
    return route.decision


@app.command()
def init(
    config_path: Annotated[Path | None, typer.Option(help="Config file path.")] = None,
) -> None:
    """Interactive setup: credentials, connection test, boards, Inbox/labels."""
    console.print("Step 1/4 — credentials. See SETUP.md for how to get them.")
    key = typer.prompt("Trello API key").strip()
    token = typer.prompt("Trello token", hide_input=True).strip()
    if not key or not token:
        console.print("[red]Error:[/red] key and token must not be empty.")
        raise typer.Exit(1)

    console.print("Step 2/4 — testing the connection…")
    try:
        with TrelloClient(key, token) as client:
            me = client.get_me()
            console.print(f"[green]Connected[/green] as {me.get('username', '?')}.")
            boards = client.list_boards()
    except (TrelloAuthError, TrelloError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    if not boards:
        console.print("[red]Error:[/red] no open boards found for this account.")
        raise typer.Exit(1)

    console.print("Step 3/4 — pick boards to use:")
    for i, board in enumerate(boards, start=1):
        console.print(f"  {i}. {board.name}")
    choice = typer.prompt("Board numbers (comma-separated, Enter = all)", default="").strip()
    wanted = parse_board_choice(choice, boards)
    if wanted is None:
        console.print("[red]Error:[/red] could not parse the board selection.")
        raise typer.Exit(1)
    settings = config_mod.Settings(board_ids=[b.id for b in wanted])

    console.print("Step 4/4 — Inbox list and sorting label:")
    try:
        with TrelloClient(key, token) as client:
            for board in wanted:
                ensure_board_fallbacks(client, board, settings)
    except (TrelloAuthError, TrelloError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    saved = config_mod.save_settings(settings, config_path)
    console.print(f"[green]Settings saved[/green] to {saved}.")
    if typer.confirm("Save TRELLO_KEY/TRELLO_TOKEN to ./.env?", default=False):
        env_path = Path(".env")
        env_path.write_text(f"TRELLO_KEY={key}\nTRELLO_TOKEN={token}\n", encoding="utf-8")
        env_path.chmod(0o600)
        console.print(
            "[green]Saved[/green] to ./.env (gitignored — never commit it). "
            "Otherwise export the variables in your shell profile; see SETUP.md."
        )
    else:
        console.print("Set TRELLO_KEY and TRELLO_TOKEN in your environment or .env; see SETUP.md.")
    console.print('Next: `t sync`, then `t add "úkol, do pátku" --dry-run`.')


def parse_board_choice(choice: str, boards: list[Board]) -> list[Board] | None:
    """Parse '1,3' style selection; empty string means all boards."""
    if not choice:
        return list(boards)
    picked: list[Board] = []
    for part in choice.split(","):
        part = part.strip()
        if not part.isdigit():
            return None
        index = int(part) - 1
        if index < 0 or index >= len(boards) or boards[index] in picked:
            return None
        picked.append(boards[index])
    return picked


def ensure_board_fallbacks(
    client: TrelloClient, board: Board, settings: config_mod.Settings
) -> None:
    """Offer to create the Inbox list / sorting label when missing."""
    lists = client.list_lists(board.id)
    labels = client.list_labels(board.id)
    if not any(lst.name.lower() == settings.inbox_list_name.lower() for lst in lists):
        if typer.confirm(
            f"Create list {settings.inbox_list_name!r} on board {board.name!r}?",
            default=True,
        ):
            created = client.create_list(board.id, settings.inbox_list_name)
            console.print(f"  Created list {created.name!r}.")
    if not any(lb.name.lower() == settings.needs_sorting_label.lower() for lb in labels):
        if typer.confirm(
            f"Create label {settings.needs_sorting_label!r} on board {board.name!r}?",
            default=True,
        ):
            created_label = client.create_label(board.id, settings.needs_sorting_label, "yellow")
            console.print(f"  Created label {created_label.name!r}.")


@app.command()
def mcp(
    http: Annotated[bool, typer.Option(help="Use streamable HTTP instead of stdio.")] = False,
    host: Annotated[str, typer.Option(help="HTTP bind host (localhost only by default).")] = (
        "127.0.0.1"
    ),
    port: Annotated[int, typer.Option(help="HTTP port.")] = 8000,
) -> None:
    """Serve the t pipeline to AI agents over MCP (stdio by default)."""
    from t import mcp_server as mcp_server_mod

    if not http:
        mcp_server_mod.run_stdio()
        return
    try:
        mcp_server_mod.run_http(host, port)
    except mcp_server_mod.McpStartupError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e


@app.command()
def log(
    limit: Annotated[int, typer.Option(help="How many recent entries to show.")] = 10,
) -> None:
    """Show recent classification decisions."""
    entries = read_last(config_mod.default_log_path(), limit)
    if not entries:
        console.print("No decisions logged yet.")
        return
    table = Table(title=f"Last {len(entries)} decision(s)")
    table.add_column("Time")
    table.add_column("Title")
    table.add_column("Confidence")
    table.add_column("Card")
    for entry in entries:
        table.add_row(
            entry.ts.strftime("%Y-%m-%d %H:%M"),
            entry.decision.title,
            f"{entry.decision.confidence:.2f}",
            entry.card_url or entry.card_id or "(dry run?)",
        )
    console.print(table)


if __name__ == "__main__":
    main()
