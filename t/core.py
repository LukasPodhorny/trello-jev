"""Shared task pipeline used by the CLI and the MCP server.

Pure with respect to stdio: never prints, never prompts. User-facing notes
come back as data (``warnings``) or typed exceptions; each caller renders
them for its own channel.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel

from t import config as config_mod
from t.classify.base import Decision
from t.classify.jev import JevClassifier
from t.classify.jev_dates import JevDateResult
from t.classify.mock import MockClassifier
from t.classify.schema import SchemaError, StructureSchema, build_schema
from t.dates import PRAGUE, parse_due
from t.decision_log import DecisionLogEntry, append_log
from t.trello import cache as cache_mod
from t.trello.cache import BoardStructure, CachedStructure, utcnow
from t.trello.client import TrelloClient

CLASSIFIERS = ("mock", "jev")
DATE_ENGINES = ("jev", "parser")


class TaskPlan(BaseModel):
    """Everything ``t add`` figures out before touching Trello writes."""

    decision: Decision
    board_id: str
    board_name: str
    list_id: str
    list_name: str
    label_ids: list[str]
    label_names: list[str]
    due: datetime | None
    routed_to_inbox: bool
    inbox_label_name: str | None = None
    classifier_confidence: float
    date_confidence: float | None
    date_engine: str
    warnings: list[str] = []
    jev_date: JevDateResult | None = None


class AddResult(TaskPlan):
    """A plan plus the created card (None ids when dry run)."""

    card_id: str | None = None
    card_url: str | None = None


class InboxRoute(BaseModel):
    """Outcome of the low-confidence Inbox fallback (no printing here)."""

    decision: Decision
    routed: bool
    inbox_name: str | None = None
    label_name: str | None = None


def sync_structure(
    secrets: config_mod.Secrets,
    settings: config_mod.Settings,
    cache_path: Path,
    *,
    backoff_base: float = 1.0,
) -> CachedStructure:
    """Download boards/lists/labels and store them in the cache file."""
    wanted = set(settings.board_ids)
    structures: list[BoardStructure] = []
    with TrelloClient(
        secrets.trello_key, secrets.trello_token, backoff_base=backoff_base
    ) as client:
        for board in client.list_boards():
            if wanted and board.id not in wanted:
                continue
            structures.append(
                BoardStructure(
                    board=board,
                    lists=client.list_lists(board.id),
                    labels=client.list_labels(board.id),
                )
            )
    cached = CachedStructure(fetched_at=cache_mod.utcnow(), boards=structures)
    cache_mod.save_cache(cached, cache_path)
    return cached


def ensure_fresh_cache(
    secrets: config_mod.Secrets,
    settings: config_mod.Settings,
    cache_path: Path,
) -> CachedStructure:
    """Return the cache, re-syncing first when it is missing or stale."""
    cached = cache_mod.load_cache(cache_path)
    if cached is None or not cache_mod.is_fresh(cached, settings.cache_ttl_seconds):
        return sync_structure(secrets, settings, cache_path)
    return cached


def apply_date_gate(result: JevDateResult, threshold: float) -> tuple[datetime | None, str | None]:
    """Turn a Jev date result into a due datetime or a reason for having none.

    Returns (due, warning): a confidently dateless text is silent (like the
    parser path with no phrase); anything else without a usable date explains
    why. List placement is never touched here.
    """
    if result.date is not None and result.confidence >= threshold:
        d = result.date
        return datetime(d.year, d.month, d.day, tzinfo=PRAGUE), None
    if result.date is None and result.mode == "none" and result.confidence >= threshold:
        return None, None
    if result.date is None:
        return None, f"Jev date parts don't assemble ({result.note}); no due date."
    return (
        None,
        f"Jev date confidence {result.confidence:.2f} is below "
        f"the {threshold:.2f} threshold; no due date.",
    )


def route_to_inbox(
    decision: Decision,
    schema: StructureSchema,
    settings: config_mod.Settings,
) -> InboxRoute:
    """Route low-confidence decisions to the Inbox list with a sorting label."""
    if decision.confidence >= settings.threshold:
        return InboxRoute(decision=decision, routed=False)
    board = schema.board(decision.board_id)
    inbox = next(
        (lst for lst in board.lists if lst.name.lower() == settings.inbox_list_name.lower()),
        None,
    )
    if inbox is None:
        raise SchemaError(
            f"Low confidence ({decision.confidence:.2f} < {settings.threshold:.2f}) "
            f"but list {settings.inbox_list_name!r} does not exist on board "
            f"{board.name!r}; run `t init` to create it."
        )
    label = next(
        (lb for lb in board.labels if lb.name.lower() == settings.needs_sorting_label.lower()),
        None,
    )
    if label is None:
        raise SchemaError(
            f"Low confidence ({decision.confidence:.2f} < {settings.threshold:.2f}) "
            f"but label {settings.needs_sorting_label!r} does not exist on board "
            f"{board.name!r}; run `t init` to create it."
        )
    label_ids = list(decision.label_ids)
    if label.id not in label_ids:
        label_ids.append(label.id)
    return InboxRoute(
        decision=decision.model_copy(update={"list_id": inbox.id, "label_ids": label_ids}),
        routed=True,
        inbox_name=inbox.name,
        label_name=label.name,
    )


def plan_task(
    text: str,
    *,
    board: str | None,
    classifier: str,
    engine: str,
    settings: config_mod.Settings,
    secrets: config_mod.Secrets,
    cache_path: Path,
    today: date,
) -> TaskPlan:
    """Run the read-only half of ``t add``: sync, classify, resolve the date."""
    if classifier not in CLASSIFIERS:
        raise ValueError(f"Unknown classifier {classifier!r} (expected 'mock' or 'jev').")
    if engine not in DATE_ENGINES:
        raise ValueError(f"Unknown date engine {engine!r} (expected 'jev' or 'parser').")
    cached = ensure_fresh_cache(secrets, settings, cache_path)
    schema = build_schema(cached, board)
    jev_date: JevDateResult | None = None
    if classifier == "mock":
        decision = MockClassifier().decide(text, schema)
    else:
        with JevClassifier(secrets.jev_api_key, model=settings.jev_model) as jev:
            if engine == "jev":
                decision, jev_date = jev.decide_with_dates(text, schema, today)
            else:
                decision = jev.decide(text, schema)
    schema.validate_decision(decision)
    route = route_to_inbox(decision, schema, settings)
    decision = route.decision
    warnings: list[str] = []
    due: datetime | None
    date_confidence: float | None = None
    if engine == "parser":
        due = parse_due(decision.due_phrase, utcnow())
        if decision.due_phrase and due is None:
            warnings.append(
                f"could not parse {decision.due_phrase!r}; the card will have no due date."
            )
    else:
        if jev_date is None:  # mock classifier: date-only Jev request
            with JevClassifier(secrets.jev_api_key, model=settings.jev_model) as jev:
                jev_date = jev.extract_date(text, today)
        due, warning = apply_date_gate(jev_date, settings.threshold)
        if warning is not None:
            warnings.append(warning)
        date_confidence = jev_date.confidence
    resolved = schema.board(decision.board_id)
    list_name = next(
        (lst.name for lst in resolved.lists if lst.id == decision.list_id), decision.list_id
    )
    label_names = [
        next((lb.name or lb.id for lb in resolved.labels if lb.id == lid), lid)
        for lid in decision.label_ids
    ]
    return TaskPlan(
        decision=decision,
        board_id=resolved.id,
        board_name=resolved.name,
        list_id=decision.list_id,
        list_name=list_name,
        label_ids=list(decision.label_ids),
        label_names=label_names,
        due=due,
        routed_to_inbox=route.routed,
        inbox_label_name=route.label_name,
        classifier_confidence=decision.confidence,
        date_confidence=date_confidence,
        date_engine=engine,
        warnings=warnings,
        jev_date=jev_date,
    )


def add_task(
    text: str,
    *,
    dry_run: bool,
    board: str | None = None,
    source: str,
    classifier: str = "mock",
    date_engine: str | None = None,
    settings: config_mod.Settings | None = None,
    secrets: config_mod.Secrets | None = None,
    cache_path: Path | None = None,
    log_path: Path | None = None,
    today: date | None = None,
) -> AddResult:
    """File one task through the normal pipeline, creating the card unless dry run."""
    resolved_settings = settings or config_mod.load_settings()
    resolved_secrets = secrets or config_mod.load_secrets()
    target = cache_path or config_mod.default_cache_path()
    log_target = log_path or config_mod.default_log_path()
    engine = date_engine or resolved_settings.date_engine
    plan = plan_task(
        text,
        board=board,
        classifier=classifier,
        engine=engine,
        settings=resolved_settings,
        secrets=resolved_secrets,
        cache_path=target,
        today=today or datetime.now(PRAGUE).date(),
    )
    if dry_run:
        return AddResult(**plan.model_dump())
    return finalize_plan(
        plan, input_text=text, source=source, secrets=resolved_secrets, log_path=log_target
    )


def finalize_plan(
    plan: TaskPlan,
    *,
    input_text: str,
    source: str,
    secrets: config_mod.Secrets,
    log_path: Path,
) -> AddResult:
    """Create the Trello card for an already-made plan and log the decision."""
    with TrelloClient(secrets.trello_key, secrets.trello_token) as client:
        card = client.create_card(
            list_id=plan.decision.list_id,
            name=plan.decision.title,
            desc=plan.decision.description,
            due=plan.due.isoformat() if plan.due else None,
            label_ids=plan.decision.label_ids or None,
        )
    append_log(
        DecisionLogEntry(
            ts=utcnow(),
            input_text=input_text,
            decision=plan.decision,
            card_id=card.id,
            card_url=card.url,
            date_engine=plan.date_engine,
            source=source,
        ),
        log_path,
    )
    return AddResult(**plan.model_dump(), card_id=card.id, card_url=card.url)
