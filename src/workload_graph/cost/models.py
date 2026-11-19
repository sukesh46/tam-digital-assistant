"""Cost models: access plan, collection scope and normalised cost facts.

Status: real
Purpose: the typed inputs and outputs of the cost collectors and normalisers (Section 7.1).
Inputs: validated values from the caller or a normaliser.
Outputs: ``AccessPlan``, ``CostScope``, ``CostFact``, ``CostFactKind``.
Planned dependencies: pydantic, ids, periods.
Notes: amounts are ``Decimal`` and serialise as strings. ``dimensions`` is stored with sorted keys.
    # DECISION-PENDING: per-account or payer mode depends on who owns the management account.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from workload_graph.cost.periods import Period
from workload_graph.ids import AccountId
from workload_graph.models import Evidence

CostFactKind = Literal["account_service_month", "account_window_total", "tag_month",
                       "resource_day"]


class AccessPlan(BaseModel):
    """Whose credentials make the calls, and whose spend is requested.

    Per-account mode is ``(A, [A])``; payer mode is ``(P, [A, B, C])``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    access_account_id: AccountId
    linked_account_ids: tuple[AccountId, ...] = Field(min_length=1)

    @field_validator("linked_account_ids")
    @classmethod
    def _sorted_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))


class CostScope(BaseModel):
    """What one cost collection may request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: date
    lookback_months: int = Field(ge=1, le=12)
    metric: str
    max_calls: int = Field(ge=1)
    resource_level_window_days: int = Field(ge=1, le=14)
    resource_level_top_services: int = Field(ge=0)
    tag_keys: tuple[str, ...] = ()


class CostFact(BaseModel):
    """One amount from Cost Explorer, with the dimensions it was grouped by."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str
    kind: CostFactKind
    dimensions: dict[str, str]
    period: Period
    amount: Decimal
    unit: str
    estimated: bool
    evidence: Evidence

    @field_validator("dimensions")
    @classmethod
    def _sorted_keys(cls, value: dict[str, str]) -> dict[str, str]:
        return {key: value[key] for key in sorted(value)}

    def sort_key(self) -> tuple[str, str, str, str]:
        """Stable ordering key."""
        dims = "|".join(f"{k}={v}" for k, v in self.dimensions.items())
        return (self.kind, self.period.start.isoformat(), dims, self.fact_id)
