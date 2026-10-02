"""Czech date-extraction eval: run eval.jsonl against one date engine.

Usage (from the repo root):

    uv run python examples/run_eval.py [--engine jev|parser]

The Jev engine needs JEV_API_KEY (environment or .env) and makes one paid
request per case. Exits 1 when any case fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from t.classify.jev import JevClassifier  # noqa: E402
from t.classify.jev_dates import resolve_weekday  # noqa: E402
from t.classify.phrases import extract_due_phrase  # noqa: E402
from t.dates import PRAGUE, parse_due  # noqa: E402


def expected_date(spec: dict[str, object], today: date) -> date:
    kind = spec.get("kind")
    if kind == "offset_days":
        days = spec.get("days")
        assert isinstance(days, int)
        return today + timedelta(days=days)
    if kind == "weekday":
        weekday = spec.get("weekday")
        offset = spec.get("offset")
        assert isinstance(weekday, str) and isinstance(offset, str)
        return resolve_weekday(today, weekday, offset)
    if kind == "next_week_monday":
        this_monday = today - timedelta(days=today.weekday())
        return this_monday + timedelta(days=7)
    raise ValueError(f"Unknown expect kind: {kind!r}")


def run_jev(text: str, today: date, key: str) -> tuple[date | None, str]:
    with JevClassifier(key) as jev:
        result = jev.extract_date(text, today)
    detail = f"conf={result.confidence:.2f} mode={result.mode} note={result.note!r}"
    return result.date, detail


def run_parser(text: str, now: datetime) -> tuple[date | None, str]:
    _, phrase = extract_due_phrase(text)
    due = parse_due(phrase, now)
    return (due.date() if due else None), f"phrase={phrase!r}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Czech date-extraction eval.")
    parser.add_argument("--engine", choices=("jev", "parser"), default="jev")
    args = parser.parse_args()

    load_dotenv()
    today = datetime.now(PRAGUE).date()
    now = datetime.now(PRAGUE)
    key = os.environ.get("JEV_API_KEY", "")
    if args.engine == "jev" and not key:
        print("error: JEV_API_KEY is not set (env or .env).", file=sys.stderr)
        return 2

    path = Path(__file__).resolve().parent / "eval.jsonl"
    failures = 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += 1
        case = json.loads(line)
        text = case["input"]
        expected = expected_date(case["expect"], today)
        if args.engine == "jev":
            got, detail = run_jev(text, today, key)
        else:
            got, detail = run_parser(text, now)
        status = "PASS" if got == expected else "FAIL"
        if got != expected:
            failures += 1
        print(f"[{status}] {text!r}: expected {expected}, got {got} ({detail})")
    print(f"{total - failures}/{total} passed (engine={args.engine}, today={today}).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
