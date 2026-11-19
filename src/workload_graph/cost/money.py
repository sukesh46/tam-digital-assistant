"""Money and ratio helpers. ``Decimal`` only.

Status: real
Purpose: parse, sum, round and format amounts without binary floating point (F-D1, F-D3).
Inputs: amounts as ``str``, ``int``, ``Decimal`` (a binary float is converted through ``str``).
Outputs: ``parse_amount``, ``quantize_cents``, ``format_money``, ``format_pct``, ``safe_ratio``,
    ``sum_same_unit``, ``CurrencyMismatchError``.
Planned dependencies: none.
Notes: rounding to cents happens only for presentation and summary figures. Sums use unrounded
    values. ``CurrencyMismatchError`` is the brief's ``CurrencyMismatch`` (the lint rules require
    the ``Error`` suffix).
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
_PCT_STEP = Decimal("0.1")


class CurrencyMismatchError(ValueError):
    """Two amounts with different units were about to be combined."""


def parse_amount(value: object) -> Decimal:
    """Parse an amount. Rejects ``bool``, ``None``, NaN and infinities. Negative is allowed."""
    if isinstance(value, bool) or value is None:
        raise ValueError("not an amount")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int | str):
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            raise ValueError("not an amount") from None
    elif isinstance(value, float):
        parsed = Decimal(str(value))
    else:
        raise ValueError("not an amount")
    if not parsed.is_finite():
        raise ValueError("amount must be finite")
    return parsed


def quantize_cents(amount: Decimal) -> Decimal:
    """Round half up to 0.01. Presentation and summary figures only."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def format_money(amount: Decimal, unit: str) -> str:
    """``USD 6,900.00``; negative as ``-USD 120.00``."""
    rounded = quantize_cents(amount)
    sign = "-" if rounded < 0 else ""
    return f"{sign}{unit} {abs(rounded):,.2f}"


def format_pct(ratio: Decimal | None) -> str:
    """A ratio as a percentage with one decimal place (``0.092`` -> ``9.2%``). None is n/a."""
    if ratio is None:
        return "n/a"
    return f"{(ratio * 100).quantize(_PCT_STEP, rounding=ROUND_HALF_UP)}%"


def safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """``numerator / denominator``, or None when the denominator is zero."""
    if denominator == 0:
        return None
    return numerator / denominator


def sum_same_unit(items: Iterable[tuple[Decimal, str]]) -> tuple[Decimal, str | None]:
    """Sum amounts that share one unit. Raises ``CurrencyMismatchError`` on mixed units."""
    total = Decimal(0)
    unit: str | None = None
    for amount, item_unit in items:
        if unit is None:
            unit = item_unit
        elif item_unit != unit:
            raise CurrencyMismatchError(f"{unit} and {item_unit} cannot be summed")
        total += amount
    return total, unit
