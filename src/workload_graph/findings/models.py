"""Finding records and confidence.

Status: real
Purpose: one cited statement per finding, traceable to raw responses (Section 7.3).
Inputs: values computed by the findings rules.
Outputs: ``Confidence``, ``min_confidence``, ``FindingKind``, ``Finding``.
Planned dependencies: pydantic, cost.money.
Notes: ``finding_id`` is ``F-`` plus 12 hex of SHA-256 over the canonical JSON of the rule id,
    rule version, kind, workload id, account id and subject keys, so it is stable across runs.
    ``display_id`` (``F-001``) is assigned after the final sort and is for presentation only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from workload_graph.cost.money import format_money, format_pct
from workload_graph.models import Evidence

Tier = Literal["quick_win", "plan_first"]
Value = str | int | bool | None


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        """3 for high, 2 for medium, 1 for low."""
        return {"high": 3, "medium": 2, "low": 1}[self.value]


def min_confidence(a: Confidence, b: Confidence) -> Confidence:
    """The lower of two confidences (F-D13: never inflate)."""
    return a if a.rank <= b.rank else b


class FindingKind(StrEnum):
    SPEND_CHANGE = "spend_change"
    SPEND_DRIVER = "spend_driver"
    WORKLOAD_SPEND = "workload_spend"
    SAVINGS_OPPORTUNITY = "savings_opportunity"
    COMMITMENT_OPPORTUNITY = "commitment_opportunity"
    DATA_QUALITY = "data_quality"


def finding_id_for(rule_id: str, rule_version: str, kind: str, workload_id: str | None,
                   account_id: str | None, subject: Mapping[str, str]) -> str:
    """Stable id from the rule and the subject of a finding."""
    canonical = json.dumps(
        {"rule_id": rule_id, "rule_version": rule_version, "kind": kind,
         "workload_id": workload_id, "account_id": account_id, "subject": dict(subject)},
        sort_keys=True, separators=(",", ":"))
    return "F-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


class Finding(BaseModel):
    """One finding. Every finding carries at least one evidence record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_id: str
    display_id: str | None = None
    kind: FindingKind
    rule_id: str
    rule_version: str
    pillar: Literal["cost_optimization"] = "cost_optimization"
    workload_id: str | None
    account_id: str | None
    confidence: Confidence
    tier: Tier | None = None
    values: dict[str, Value]
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    flags: tuple[str, ...] = ()
    supports_causal_claim: bool = False

    @field_validator("flags")
    @classmethod
    def _sorted_flags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @field_validator("values")
    @classmethod
    def _sorted_values(cls, value: dict[str, Value]) -> dict[str, Value]:
        return {key: value[key] for key in sorted(value)}

    @field_validator("evidence")
    @classmethod
    def _sorted_evidence(cls, value: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
        unique = {e.sort_key(): e for e in value}
        return tuple(unique[k] for k in sorted(unique))

    @classmethod
    def create(cls, *, kind: FindingKind, rule_id: str, rule_version: str,
               workload_id: str | None, account_id: str | None, confidence: Confidence,
               values: Mapping[str, Value], evidence: tuple[Evidence, ...],
               subject: Mapping[str, str], tier: Tier | None = None,
               flags: tuple[str, ...] = (), supports_causal_claim: bool = False) -> Finding:
        """Build a finding and compute its stable id."""
        return cls(
            finding_id=finding_id_for(rule_id, rule_version, kind.value, workload_id,
                                      account_id, subject),
            kind=kind, rule_id=rule_id, rule_version=rule_version, workload_id=workload_id,
            account_id=account_id, confidence=confidence, tier=tier, values=dict(values),
            evidence=evidence, flags=flags, supports_causal_claim=supports_causal_claim,
        )

    def amount(self) -> Decimal | None:
        """The primary monetary value (``values['amount']``), if any."""
        raw = self.values.get("amount")
        if not isinstance(raw, str):
            return None
        try:
            return Decimal(raw)
        except InvalidOperation:
            return None

    def unit(self) -> str:
        """Currency unit of the finding's amounts (``USD`` when not stated)."""
        unit = self.values.get("unit")
        return unit if isinstance(unit, str) and unit else "USD"

    def rendered_values(self) -> frozenset[str]:
        """Every string the guardrail accepts as a number taken from this finding."""
        rendered: set[str] = set()
        for value in self.values.values():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, int):
                rendered.update({str(value), f"{value:,}"})
                continue
            try:
                number = Decimal(value)
            except InvalidOperation:
                continue
            if not number.is_finite():
                continue
            rendered.update({format_money(number, self.unit()), format_pct(number)})
            if number == number.to_integral_value():
                rendered.update({str(int(number)), f"{int(number):,}"})
        return frozenset(rendered)
