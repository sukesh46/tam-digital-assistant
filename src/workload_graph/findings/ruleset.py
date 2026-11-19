"""Versioned, hashed ruleset for the cost findings (Section 9).

Status: real
Purpose: every threshold the findings rules use, in one place, versioned and hashed (F-D4).
Inputs: JSON file or defaults.
Outputs: ``FindingsRuleset``, ``NotEnabledMatcher``.
Planned dependencies: pydantic.
Notes: every default is provisional. The workload ``Ruleset`` is untouched; both hashes appear in
    the report.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

FINDINGS_RULESET_VERSION = "0.1.0"

Ratio = Annotated[Decimal, Field(ge=Decimal(0), le=Decimal(1))]
Level = Literal["high", "medium", "low"]
Method = Literal["tag", "resource_level", "sole_account"]


class NotEnabledMatcher(BaseModel):
    """An error that means a feature is switched off, not that access is denied."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message_contains: str = ""

    def matches(self, code: str, message: str) -> bool:
        """True when the code is equal and the message holds the substring (any case)."""
        return code == self.code and self.message_contains.lower() in message.lower()


def _default_matchers() -> list[NotEnabledMatcher]:
    # ASSUMPTION: the errors for "resource-level data not enabled" and "Cost Optimization Hub
    # not opted in" are unverified (assumption 4 in docs/ASSUMPTIONS.md). Matched by code and
    # message substring so a correction is a data change.
    return [
        NotEnabledMatcher(code="DataUnavailableException"),
        NotEnabledMatcher(code="ValidationException", message_contains="resource level"),
        NotEnabledMatcher(code="ValidationException", message_contains="opt"),
        NotEnabledMatcher(code="AccessDeniedException", message_contains="enroll"),
        NotEnabledMatcher(code="AccessDeniedException", message_contains="opt"),
    ]


class FindingsRuleset(BaseModel):
    """Thresholds and settings for the cost findings. All defaults provisional."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    version: str = FINDINGS_RULESET_VERSION
    # SKELETON: provisional
    lookback_months: int = Field(default=3, ge=2, le=12)
    # SKELETON: provisional
    # DECISION-PENDING: amortised cost matters for commitments.
    metric: str = "UnblendedCost"
    # SKELETON: provisional
    driver_top_n: int = Field(default=3, ge=1)
    # SKELETON: provisional
    driver_min_abs: Decimal = Field(default=Decimal("100.00"), ge=Decimal(0))
    # SKELETON: provisional (F-D17: 13, not 14, until the edge semantics are verified)
    resource_level_window_days: int = Field(default=13, ge=1, le=14)
    # SKELETON: provisional
    resource_level_top_services: int = Field(default=5, ge=0)
    # SKELETON: provisional
    tag_min_member_share: Ratio = Decimal("0.8")
    # SKELETON: provisional
    sole_workload_max_unplaced_ratio: Ratio = Decimal("0.2")
    # SKELETON: provisional
    attribution_low_explained_ratio: Ratio = Decimal("0.5")
    # SKELETON: provisional
    attribution_disagreement_ratio: Ratio = Decimal("0.25")
    # SKELETON: provisional
    unplaced_share_flag: Ratio = Decimal("0.3")
    # SKELETON: provisional
    quick_win_efforts: list[str] = Field(default=["VeryLow", "Low"], min_length=1)
    # SKELETON: provisional
    commitment_action_types: list[str] = Field(
        default=["PurchaseSavingsPlans", "PurchaseReservedInstances"], min_length=1)
    # SKELETON: provisional
    stale_recommendation_days: int = Field(default=14, ge=1)
    # SKELETON: provisional
    savings_dedupe_tolerance: Decimal = Field(default=Decimal("0.01"), ge=Decimal(0))
    # SKELETON: provisional
    method_confidence: dict[Method, Level] = Field(
        default={"tag": "medium", "resource_level": "high", "sole_account": "low"})
    not_enabled_matchers: list[NotEnabledMatcher] = Field(default_factory=_default_matchers)
    # SKELETON: provisional. Cost Explorer charges per paginated request.
    max_calls: int = Field(default=40, ge=1)
    # SKELETON: provisional (F-D16)
    cache_ttl_hours: int = Field(default=24, ge=0)

    def not_enabled(self, code: str, message: str) -> bool:
        """True when an error means the feature is switched off."""
        return any(m.matches(code, message) for m in self.not_enabled_matchers)

    def canonical_json(self) -> str:
        """Canonical JSON: sorted keys, no whitespace."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        """SHA-256 hex digest of the canonical JSON."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def save(self, path: Path) -> None:
        """Write as pretty, sorted JSON."""
        path.write_text(json.dumps(self.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
                        encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> FindingsRuleset:
        """Load and validate a findings ruleset file."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
