"""Deterministic date parsing: model never computes dates.

``parse_due`` turns the raw ``due_phrase`` (cs or en) into a timezone-aware
datetime in Europe/Prague, relative to ``now``. Unparseable input yields
None and the card is created without a due date.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import dateparser

PRAGUE_TZ = "Europe/Prague"
PRAGUE = ZoneInfo(PRAGUE_TZ)

#: dateparser (cs) understands "v pátek" but not "do pátku", so deadline-style
#: phrases are normalized to the "on the day" form (same due date).
_DO_DAY_NORMALIZATION: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdo\s+pondělí\b", re.IGNORECASE), "v pondělí"),
    (re.compile(r"\bdo\s+úterý\b", re.IGNORECASE), "v úterý"),
    (re.compile(r"\bdo\s+středy\b", re.IGNORECASE), "ve středu"),
    (re.compile(r"\bdo\s+čtvrtka\b", re.IGNORECASE), "ve čtvrtek"),
    (re.compile(r"\bdo\s+pátku\b", re.IGNORECASE), "v pátek"),
    (re.compile(r"\bdo\s+soboty\b", re.IGNORECASE), "v sobotu"),
    (re.compile(r"\bdo\s+neděle\b", re.IGNORECASE), "v neděli"),
)


def normalize_do_phrase(phrase: str) -> str:
    """Rewrite "do <day>" deadline phrases into dateparser-readable form."""
    out = phrase
    for pattern, replacement in _DO_DAY_NORMALIZATION:
        out = pattern.sub(replacement, out)
    return out


def parse_due(phrase: str | None, now: datetime) -> datetime | None:
    """Parse a raw due phrase; None in → None out, garbage → None."""
    if phrase is None or not phrase.strip():
        return None
    base = now.astimezone(PRAGUE).replace(tzinfo=None)
    parsed = dateparser.parse(
        normalize_do_phrase(phrase.strip()),
        languages=["cs", "en"],
        settings={
            "TIMEZONE": PRAGUE_TZ,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": base,
            "PREFER_DATES_FROM": "future",
        },
    )
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=PRAGUE)
    return parsed.astimezone(PRAGUE)
