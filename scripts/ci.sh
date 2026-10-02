#!/usr/bin/env bash
# CI: lint + types + tests. Fails fast on the first red gate.
set -euo pipefail
cd "$(dirname "$0")/.."

uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy t tests
uv run --frozen pytest
