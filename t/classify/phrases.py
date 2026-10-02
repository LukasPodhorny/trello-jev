"""Shared raw due-phrase spotting (cs/en).

The model never computes dates; this only cuts the raw phrase out of the
input sentence so ``dates.py`` can parse it and the title stays clean.
Used by both MockClassifier and JevClassifier.
"""

from __future__ import annotations

import re

#: Raw due phrases spottable in one sentence.
DUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(dnes|zítra|pozítří)\b(?:\s+v\s+\d{1,2}(:\d{2})?)?", re.IGNORECASE),
    re.compile(
        r"\b(do|v|ve|k|ke|na)\s+"
        r"(pondělí|úterý|střed\w+|čtvrt\w+|pát\w+|sobot\w+|neděl\w+"
        r"|konce\s+týdne|konce\s+měsíce|příští\s+týden)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bza\s+(\d+|jeden|dva|dvě|tři|týden|měsíc|\w+)\s+"
        r"(minut|hodin|den|dní|týden|týdny|týdnů|měsíc)\w*",
        re.IGNORECASE,
    ),
    re.compile(r"\bv\s+\d{1,2}:\d{2}\b", re.IGNORECASE),
)


def extract_due_phrase(text: str) -> tuple[str, str | None]:
    """Return (title_remainder, due_phrase|None)."""
    for pattern in DUE_PATTERNS:
        match = pattern.search(text)
        if match:
            phrase = match.group(0).strip()
            remainder = (text[: match.start()] + text[match.end() :]).strip(" ,;")
            return remainder, phrase
    return text, None
