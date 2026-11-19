"""Cost Optimization Hub normaliser (pure) and the ``Recommendation`` model.

Status: real
Purpose: turn hub pages into ``Recommendation`` records and per-account deduplicated savings
    totals (Section 10.4).
Inputs: a ``cost_optimization_hub`` ``RawSnapshot``.
Outputs: ``CostOptimizationHubBatch``, ``Recommendation``, ``DedupedSummary``.
Planned dependencies: cost.money, cost.labels.
Notes:
    - Missing optional fields stay ``None``. They are never defaulted to a convenient value.
    - A recommendation without a resource ARN or id is kept (it may be a commitment purchase).
    - A summary without a deduplicated total yields a note, not a zero.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from workload_graph.cost.labels import LIST_RECOMMENDATIONS, SUMMARY_PREFIX, split_payload_key
from workload_graph.cost.money import parse_amount
from workload_graph.cost.snapshots import escape_pointer
from workload_graph.ids import AccountId
from workload_graph.models import Evidence, RawSnapshot
from workload_graph.normalisers.base import evidence
from workload_graph.normalisers.cost_explorer import label_statuses


class Recommendation(BaseModel):
    """One hub recommendation (Section 7.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recommendation_id: str
    account_id: AccountId
    region: str
    resource_arn: str | None
    resource_id: str | None
    current_resource_type: str
    recommended_resource_type: str | None
    action_type: str
    estimated_monthly_savings: Decimal
    estimated_monthly_cost: Decimal | None
    estimated_savings_percentage: Decimal | None
    currency: str
    implementation_effort: str | None
    restart_needed: bool | None
    rollback_possible: bool | None
    last_refresh: AwareDatetime | None
    evidence: Evidence


class DedupedSummary(BaseModel):
    """The hub's deduplicated savings total for one account (F-D11)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: AccountId
    deduped_total: Decimal
    currency: str
    evidence: Evidence


class CostOptimizationHubBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recommendations: tuple[Recommendation, ...] = ()
    summaries: tuple[DedupedSummary, ...] = ()
    notes: tuple[str, ...] = ()
    label_status: dict[str, str] = {}


def _optional_amount(value: Any) -> Decimal | None:
    return None if value is None else parse_amount(value)


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        # ASSUMPTION: a hub timestamp without a timezone is UTC.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _recommendation(snapshot: RawSnapshot, pointer: str, item: dict[str, Any]) -> Recommendation:
    rec_id = str(item["recommendationId"])
    return Recommendation(
        recommendation_id=rec_id,
        account_id=str(item["accountId"]),
        region=str(item.get("region") or ""),
        resource_arn=_optional_str(item.get("resourceArn")),
        resource_id=_optional_str(item.get("resourceId")),
        current_resource_type=str(item["currentResourceType"]),
        recommended_resource_type=_optional_str(item.get("recommendedResourceType")),
        action_type=str(item["actionType"]),
        estimated_monthly_savings=parse_amount(item["estimatedMonthlySavings"]),
        estimated_monthly_cost=_optional_amount(item.get("estimatedMonthlyCost")),
        estimated_savings_percentage=_optional_amount(item.get("estimatedSavingsPercentage")),
        currency=str(item["currencyCode"]),
        implementation_effort=_optional_str(item.get("implementationEffort")),
        restart_needed=_optional_bool(item.get("restartNeeded")),
        rollback_possible=_optional_bool(item.get("rollbackPossible")),
        last_refresh=_timestamp(item.get("lastRefreshTimestamp")),
        evidence=evidence(snapshot, pointer, f"recommendation {rec_id}"),
    )


def normalise_cost_optimization_hub(snapshot: RawSnapshot) -> CostOptimizationHubBatch:
    """Normalise one Cost Optimization Hub snapshot."""
    recommendations: dict[str, Recommendation] = {}
    summaries: dict[str, DedupedSummary] = {}
    notes: set[str] = set()
    for key in sorted(snapshot.payload):
        if key.startswith("_"):
            continue
        _, label = split_payload_key(key)
        base = f"/payload/{escape_pointer(key)}"
        for page_index, page in enumerate(snapshot.payload[key]):
            if label == LIST_RECOMMENDATIONS:
                for item_index, item in enumerate(page.get("items") or []):
                    pointer = f"{base}/{page_index}/items/{item_index}"
                    try:
                        if not isinstance(item, dict):
                            raise ValueError("item is not an object")
                        rec = _recommendation(snapshot, pointer, item)
                    except (KeyError, ValueError, TypeError, ValidationError):
                        notes.add("cost_optimization_hub: a recommendation is missing required "
                                  "fields; skipped")
                        continue
                    recommendations.setdefault(rec.recommendation_id, rec)
            elif label.startswith(SUMMARY_PREFIX):
                account = label.removeprefix(SUMMARY_PREFIX)
                total = page.get("estimatedTotalDedupedSavings")
                if total is None:
                    notes.add(f"cost_optimization_hub: summary for {account} has no "
                              "deduplicated total")
                    continue
                try:
                    summaries[account] = DedupedSummary(
                        account_id=account, deduped_total=parse_amount(total),
                        currency=str(page.get("currencyCode") or ""),
                        evidence=evidence(snapshot, f"{base}/{page_index}",
                                          f"deduplicated savings for {account}"))
                except (ValueError, ValidationError):
                    notes.add(f"cost_optimization_hub: summary for {account} is malformed")
    return CostOptimizationHubBatch(
        recommendations=tuple(sorted(recommendations.values(),
                                     key=lambda r: (r.account_id, r.recommendation_id))),
        summaries=tuple(summaries[a] for a in sorted(summaries)),
        notes=tuple(sorted(notes)),
        label_status=label_statuses(snapshot),
    )
