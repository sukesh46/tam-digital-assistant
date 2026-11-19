"""Calendar periods with exclusive ends, as Cost Explorer uses them.

Status: real
Purpose: month arithmetic and the windows the cost collectors request (Section 10.1).
Inputs: an ``as_of`` date supplied by the caller (never the wall clock).
Outputs: ``Period``, ``month_start``, ``add_months``, ``month_end_exclusive``,
    ``complete_months``, ``current_partial_month``, ``trailing_window``.
Planned dependencies: pydantic.
Notes: hand-written month math, no third-party date library.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, model_validator


class Period(BaseModel):
    """``[start, end)``: ``end`` is exclusive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> Period:
        if self.end <= self.start:
            raise ValueError("period end must be after its start")
        return self

    def days(self) -> int:
        """Number of days covered."""
        return (self.end - self.start).days

    def label(self) -> str:
        """``2026-10-01..2026-11-01``."""
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


def month_start(day: date) -> date:
    """First day of the month containing ``day``."""
    return day.replace(day=1)


def add_months(first_of_month: date, months: int) -> date:
    """Move a first-of-month date by ``months`` (negative allowed)."""
    index = first_of_month.year * 12 + (first_of_month.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def month_end_exclusive(day: date) -> date:
    """First day of the month after the one containing ``day``."""
    return add_months(month_start(day), 1)


def complete_months(as_of: date, count: int) -> list[Period]:
    """The ``count`` complete months before the month containing ``as_of``, oldest first."""
    current = month_start(as_of)
    return [Period(start=add_months(current, -n), end=add_months(current, -n + 1))
            for n in range(count, 0, -1)]


def current_partial_month(as_of: date) -> Period | None:
    """From the first of ``as_of``'s month to ``as_of`` (exclusive); None on the first."""
    start = month_start(as_of)
    if start == as_of:
        return None
    return Period(start=start, end=as_of)


def trailing_window(as_of: date, days: int) -> Period:
    """``[as_of - days, as_of)``."""
    return Period(start=as_of - timedelta(days=days), end=as_of)
