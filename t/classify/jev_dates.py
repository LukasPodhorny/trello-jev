"""Jev date engine: read date parts with Choice questions, do math in code.

Follows https://jevwiki.ai/wiki/cookbooks/date-extraction.md: seven Choice
questions (``mode``, ``month``, ``day``, ``year``, ``day_anchor``,
``weekday``, ``week_offset``) read only what the text *says*. Pure functions
(``resolve_weekday``, ``assemble``) turn the parts into a ``date`` — the
model never does calendar arithmetic.

Deviations from the cookbook, all deliberate and minimal:

- ``role`` is "the due date of the task" (our domain has one date per text);
  question wording is otherwise verbatim, including "document".
- ``YEAR_WINDOW`` is the current year through +2 (from ``today``), not
  1900–2050: task due dates live near the present.
- ``assemble`` takes ``today`` as a required argument (production passes
  today's date in Europe/Prague; tests pin it) and does no threshold
  gating — the caller gates ``confidence`` against its own threshold.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, Field

#: What the seven questions ask about (the cookbook's ``role``).
DUE_DATE_ROLE = "the due date of the task"

MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

#: How many years past the current one the ``year`` question offers.
YEAR_WINDOW_AHEAD = 2

DATE_PART_NAMES = ("mode", "month", "day", "year", "day_anchor", "weekday", "week_offset")


class DateQuestionSpec(BaseModel):
    """Plain-data spec for one Choice question; the caller wraps it for the wire."""

    instructions: str
    criteria: dict[str, str | None]


class DatePart(BaseModel):
    """One answered part: what the text says + how sure the model is."""

    choice: str
    confidence: float = Field(ge=0.0, le=1.0)


class JevDateResult(BaseModel):
    """Assembled outcome. ``confidence`` is the min over parts actually used."""

    date: date | None
    confidence: float = Field(ge=0.0, le=1.0)
    note: str
    mode: str
    parts: dict[str, DatePart] = {}


def build_date_question_specs(today: date) -> dict[str, DateQuestionSpec]:
    """Seven typed choices that read a date's shape and parts off the text."""
    absent = "The document does not state this, or it is not this kind of date."
    role = DUE_DATE_ROLE
    years = [str(y) for y in range(today.year, today.year + YEAR_WINDOW_AHEAD + 1)]
    return {
        "mode": DateQuestionSpec(
            instructions=(
                f"How is {role} written? 'absolute' = a calendar date naming a month "
                "(e.g. 'August 14', 'the 3rd of March'); 'relative' = given relative "
                "to today (today, tomorrow, the day after tomorrow, or a named "
                "weekday such as 'next Thursday'); 'none' = the document does not "
                "state this date."
            ),
            criteria={"absolute": None, "relative": None, "none": None},
        ),
        "month": DateQuestionSpec(
            instructions=f"If {role} is an absolute calendar date, which month is it in?",
            criteria={m: None for m in MONTHS} | {"none": absent},
        ),
        "day": DateQuestionSpec(
            instructions=(
                f"If {role} is an absolute calendar date, which day of the month (1-31)?"
            ),
            criteria={str(d): None for d in range(1, 32)} | {"none": absent},
        ),
        "year": DateQuestionSpec(
            instructions=(
                f"If {role} is an absolute calendar date, which year? Pick 'none' if "
                "the document states no year (code infers it), or 'out_of_range' if "
                "a year is stated but not in the list."
            ),
            criteria={y: None for y in years}
            | {
                "out_of_range": ("A year is stated for this date but is outside the listed range."),
                "none": "No year is stated for this date.",
            },
        ),
        "day_anchor": DateQuestionSpec(
            instructions=(
                f"If {role} is relative to today, which day is it? 'today', "
                "'tomorrow', 'day_after' (the day after tomorrow), or 'weekday' "
                "(a named day of the week)."
            ),
            criteria={
                "today": None,
                "tomorrow": None,
                "day_after": None,
                "weekday": None,
                "none": absent,
            },
        ),
        "weekday": DateQuestionSpec(
            instructions=f"If {role} names a day of the week, which one?",
            criteria={w: None for w in WEEKDAYS} | {"none": absent},
        ),
        "week_offset": DateQuestionSpec(
            instructions=(
                f"If {role} names a weekday, which week is it in? 'next' for 'next "
                "Thursday' or 'Thursday next week'; 'current' for 'this Thursday'; "
                "'none' for a bare weekday with no qualifier (just 'Thursday' / "
                "'on Thursday')."
            ),
            criteria={"current": None, "next": None, "none": absent},
        ),
    }


def resolve_weekday(today: date, weekday: str, week_offset: str) -> date:
    """Which date a named weekday points to, by our stated convention: a bare
    weekday is the next occurrence on or after today; 'next' is the following
    calendar week; 'current' is this week."""
    w = WEEKDAYS.index(weekday)
    this_monday = today - timedelta(days=today.weekday())
    if week_offset == "next":
        return this_monday + timedelta(days=7 + w)
    if week_offset == "current":
        return this_monday + timedelta(days=w)
    return today + timedelta(days=(w - today.weekday()) % 7)


def assemble(parts: dict[str, DatePart], today: date) -> JevDateResult:
    """Resolve the parts Jev read into a concrete date, in code. Confidence is
    the weakest of the parts the shape actually used."""
    mode = parts["mode"].choice
    confs = [parts["mode"].confidence]

    def result(resolved: date | None, note: str) -> JevDateResult:
        confidence = min(confs)
        return JevDateResult(
            date=resolved, confidence=confidence, note=note, mode=mode, parts=parts
        )

    if mode == "none":
        return result(None, "no such date stated")

    if mode == "absolute":
        month, day, year = (
            parts["month"].choice,
            parts["day"].choice,
            parts["year"].choice,
        )
        confs += [
            parts["month"].confidence,
            parts["day"].confidence,
            parts["year"].confidence,
        ]
        if "none" in (month, day) or not day.isdigit() or month not in MONTHS:
            return result(None, "absolute date incomplete")
        if year == "out_of_range":  # a year is stated but off the list -> flag it
            return result(None, f"year outside {today.year}-{today.year + 2}")
        if year == "none":  # no year stated -> this year, bumped if well past
            try:
                resolved = date(today.year, MONTHS[month], int(day))
            except ValueError:  # e.g. February 30: inconsistent, not a real date
                return result(None, f"impossible date: {month} {day}")
            if resolved < today - timedelta(days=31):
                resolved = date(today.year + 1, MONTHS[month], int(day))
            return result(resolved, "")
        try:  # a stated, in-range year
            return result(date(int(year), MONTHS[month], int(day)), "")
        except ValueError:
            return result(None, f"impossible date: {year}-{month}-{day}")

    if mode == "relative":
        anchor = parts["day_anchor"].choice
        confs.append(parts["day_anchor"].confidence)
        if anchor == "today":
            return result(today, "")
        if anchor == "tomorrow":
            return result(today + timedelta(days=1), "")
        if anchor == "day_after":
            return result(today + timedelta(days=2), "")
        if anchor == "weekday":
            weekday, offset = parts["weekday"].choice, parts["week_offset"].choice
            confs += [parts["weekday"].confidence, parts["week_offset"].confidence]
            if weekday not in WEEKDAYS:
                return result(None, "relative weekday not read")
            return result(resolve_weekday(today, weekday, offset), "")
        return result(None, "relative day not read")

    return result(None, f"unrecognized mode: {mode}")
