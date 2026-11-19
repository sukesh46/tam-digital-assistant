"""Confidence derivation.

Status: real
Purpose: ``high`` / ``medium`` / ``low`` per workload from the share of declared members and
    the number of contributing sources.
Inputs: declared member count, total member count, sources used, ruleset.
Outputs: ``Confidence``.
Planned dependencies: none.
Notes: a workload built from a single source is capped at ``medium``. Ratios are compared
    with integer arithmetic so exact boundaries (for example 4/5 = 0.8) are not subject to
    floating point error.
"""

from __future__ import annotations

from collections.abc import Iterable
from fractions import Fraction

from workload_graph.models import Confidence
from workload_graph.ruleset import Ruleset


def derive_confidence(declared: int, total: int, sources: Iterable[str],
                      ruleset: Ruleset) -> Confidence:
    """Confidence for a workload with ``declared`` of ``total`` members declared."""
    if total <= 0:
        return "low"
    share = Fraction(declared, total)
    high = Fraction(str(ruleset.confidence_high_declared_ratio))
    medium = Fraction(str(ruleset.confidence_medium_declared_ratio))
    level: Confidence = "high" if share >= high else "medium" if share >= medium else "low"
    if level == "high" and len(set(sources)) <= 1:
        return "medium"
    return level
