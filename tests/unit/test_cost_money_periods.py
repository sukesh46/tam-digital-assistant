"""F1: money and periods. Pure, offline."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from workload_graph.cost.money import (
    CurrencyMismatchError,
    format_money,
    format_pct,
    parse_amount,
    quantize_cents,
    safe_ratio,
    sum_same_unit,
)
from workload_graph.cost.periods import (
    Period,
    add_months,
    complete_months,
    current_partial_month,
    month_end_exclusive,
    month_start,
    trailing_window,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "workload_graph"


@pytest.mark.parametrize(("value", "expected"), [
    ("12.30", Decimal("12.30")), (12, Decimal(12)), (Decimal("1.5"), Decimal("1.5")),
    (0.1, Decimal("0.1")), ("-120", Decimal("-120")),
])
def test_parse_amount_accepts_strings_ints_decimals_and_floats_through_str(
        value: object, expected: Decimal) -> None:
    assert parse_amount(value) == expected
    assert str(parse_amount(0.1)) == "0.1"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", True, None, "abc", [], ""])
def test_parse_amount_rejects_non_numbers(value: object) -> None:
    with pytest.raises(ValueError):
        parse_amount(value)


def test_quantize_cents_rounds_half_up() -> None:
    assert quantize_cents(Decimal("2.675")) == Decimal("2.68")
    assert quantize_cents(Decimal("2.674")) == Decimal("2.67")
    assert quantize_cents(Decimal("-2.675")) == Decimal("-2.68")


@pytest.mark.parametrize(("amount", "expected"), [
    (Decimal("6900"), "USD 6,900.00"), (Decimal("-120"), "-USD 120.00"),
    (Decimal("0"), "USD 0.00"), (Decimal("1234567.005"), "USD 1,234,567.01"),
])
def test_format_money(amount: Decimal, expected: str) -> None:
    assert format_money(amount, "USD") == expected


def test_format_pct() -> None:
    assert format_pct(Decimal("0.092")) == "9.2%"
    assert format_pct(Decimal("-0.0125")) == "-1.3%"
    assert format_pct(None) == "n/a"


def test_safe_ratio() -> None:
    assert safe_ratio(Decimal(1), Decimal(0)) is None
    assert safe_ratio(Decimal(1), Decimal(4)) == Decimal("0.25")


def test_sum_same_unit_refuses_mixed_units() -> None:
    assert sum_same_unit([(Decimal("1.5"), "USD"), (Decimal(2), "USD")]) == (Decimal("3.5"), "USD")
    with pytest.raises(CurrencyMismatchError):
        sum_same_unit([(Decimal(1), "USD"), (Decimal(1), "EUR")])
    assert sum_same_unit([]) == (Decimal(0), None)


def test_period_rejects_an_empty_or_reversed_range() -> None:
    with pytest.raises(ValueError):
        Period(start=date(2026, 1, 2), end=date(2026, 1, 2))
    with pytest.raises(ValueError):
        Period(start=date(2026, 1, 3), end=date(2026, 1, 2))
    assert Period(start=date(2026, 1, 1), end=date(2026, 2, 1)).days() == 31


def test_month_math_handles_year_boundaries_and_february() -> None:
    assert month_start(date(2026, 11, 19)) == date(2026, 11, 1)
    assert add_months(date(2026, 11, 1), 2) == date(2027, 1, 1)
    assert add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert month_end_exclusive(date(2028, 2, 10)) == date(2028, 3, 1)
    assert Period(start=date(2028, 2, 1), end=month_end_exclusive(date(2028, 2, 1))).days() == 29


def test_complete_months() -> None:
    months = complete_months(date(2026, 11, 19), 3)
    assert [(p.start, p.end) for p in months] == [
        (date(2026, 8, 1), date(2026, 9, 1)), (date(2026, 9, 1), date(2026, 10, 1)),
        (date(2026, 10, 1), date(2026, 11, 1))]
    across = complete_months(date(2027, 1, 5), 3)
    assert [p.start for p in across] == [date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1)]
    assert complete_months(date(2026, 3, 1), 1)[0].start == date(2026, 2, 1)


def test_current_partial_month_and_trailing_window() -> None:
    assert current_partial_month(date(2026, 11, 1)) is None
    partial = current_partial_month(date(2026, 11, 19))
    assert partial is not None
    assert (partial.start, partial.end) == (date(2026, 11, 1), date(2026, 11, 19))
    window = trailing_window(date(2026, 11, 19), 13)
    assert (window.start, window.end) == (date(2026, 11, 6), date(2026, 11, 19))
    assert window.days() == 13


def test_new_packages_use_no_float_and_no_wall_clock() -> None:
    bad = re.compile(r"\bfloat\(|:\s*float\b|->\s*float\b|datetime\.now|date\.today|time\.time")
    offenders = []
    for package in ("cost", "findings", "report"):
        for path in sorted((SRC / package).rglob("*.py")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if bad.search(line):
                    offenders.append(f"{path.name}:{number}")
    assert offenders == []
