"""Programmatic builders for cost snapshots and responses. Fake values only."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from workload_graph.cost.labels import LABELS_KEY, payload_key
from workload_graph.models import RawSnapshot

AS_OF = date(2026, 11, 19)
COLLECTED = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
METRIC = "UnblendedCost"
SERVICE_ACCOUNT = [("DIMENSION", "SERVICE"), ("DIMENSION", "LINKED_ACCOUNT")]
ACCOUNT_ONLY = [("DIMENSION", "LINKED_ACCOUNT")]
RESOURCE_ACCOUNT = [("DIMENSION", "RESOURCE_ID"), ("DIMENSION", "LINKED_ACCOUNT")]


def group(keys: list[str], amount: str | Decimal, unit: str = "USD",
          metric: str = METRIC) -> dict[str, Any]:
    return {"Keys": keys, "Metrics": {metric: {"Amount": str(amount), "Unit": unit}}}


def result(start: str, end: str, groups: list[dict[str, Any]],
           estimated: bool = False) -> dict[str, Any]:
    return {"TimePeriod": {"Start": start, "End": end}, "Total": {}, "Groups": groups,
            "Estimated": estimated}


def ce_page(results: list[dict[str, Any]], definitions: list[tuple[str, str]],
            token: str | None = None) -> dict[str, Any]:
    page: dict[str, Any] = {
        "GroupDefinitions": [{"Type": t, "Key": k} for t, k in definitions],
        "ResultsByTime": results,
        "DimensionValueAttributes": [],
    }
    if token:
        page["NextPageToken"] = token
    return page


def recommendation(rec_id: str, account: str, *, arn: str | None = None,
                   resource_id: str | None = None, action: str = "Rightsize",
                   savings: Any = "50.0", cost: Any = "200.0", effort: str | None = "Low",
                   restart: bool | None = False, rollback: bool | None = True,
                   refreshed: str | None = "2026-11-18T00:00:00+00:00",
                   currency: str = "USD", region: str = "eu-west-1") -> dict[str, Any]:
    item: dict[str, Any] = {
        "recommendationId": rec_id, "accountId": account, "region": region,
        "currentResourceType": "Ec2Instance", "recommendedResourceType": "Ec2Instance",
        "actionType": action, "estimatedMonthlySavings": Decimal(str(savings)),
        "estimatedSavingsPercentage": Decimal("25.0"), "currencyCode": currency,
        "source": "ComputeOptimizer", "tags": [],
    }
    if cost is not None:
        item["estimatedMonthlyCost"] = Decimal(str(cost))
    if arn is not None:
        item["resourceArn"] = arn
    if resource_id is not None:
        item["resourceId"] = resource_id
    if effort is not None:
        item["implementationEffort"] = effort
    if restart is not None:
        item["restartNeeded"] = restart
    if rollback is not None:
        item["rollbackPossible"] = rollback
    if refreshed is not None:
        item["lastRefreshTimestamp"] = refreshed
    return item


def summary(total: Any, currency: str = "USD") -> dict[str, Any]:
    page: dict[str, Any] = {"items": [], "groupBy": "ActionType", "currencyCode": currency}
    if total is not None:
        page["estimatedTotalDedupedSavings"] = Decimal(str(total))
    return page


def cost_snapshot(source: str, account: str, calls: dict[tuple[str, str], list[dict[str, Any]]],
                  *, statuses: dict[str, tuple[str, str, str]] | None = None,
                  snapshot_id: str | None = None, status: str = "ok") -> RawSnapshot:
    """``calls`` maps ``(operation, label)`` to pages; ``statuses`` maps a label to
    ``(operation, status, code)`` for calls that failed (they may have no pages)."""
    payload: dict[str, list[dict[str, Any]]] = {}
    labels: list[dict[str, Any]] = []
    for (operation, label), pages in calls.items():
        payload[payload_key(operation, label)] = pages
        labels.append({"label": label, "operation": operation, "status": "ok", "code": ""})
    for label, (operation, label_status, code) in (statuses or {}).items():
        labels = [entry for entry in labels if entry["label"] != label]
        labels.append({"label": label, "operation": operation, "status": label_status,
                       "code": code})
    payload[LABELS_KEY] = labels
    return RawSnapshot.model_validate({
        "snapshot_id": snapshot_id or f"{source}-{account}",
        "source": source, "account_id": account, "region": "us-east-1",
        "collected_at": COLLECTED, "operations_called": sorted({op for op, _ in calls}),
        "payload": payload, "status": status, "notes": [],
    })


# --- facts and build results for findings tests ---------------------------------------------

def fact(kind: str, dims: dict[str, str], start: str, end: str, amount: str,
         unit: str = "USD", estimated: bool = False, ref: str | None = None) -> Any:
    from workload_graph.cost.models import CostFact
    from workload_graph.cost.periods import Period
    from workload_graph.models import Evidence

    period = Period(start=date.fromisoformat(start), end=date.fromisoformat(end))
    fid = "|".join([kind, start, *[f"{k}={v}" for k, v in sorted(dims.items())]])
    return CostFact(
        fact_id=fid, kind=kind, dimensions=dims, period=period, amount=Decimal(amount),
        unit=unit, estimated=estimated,
        evidence=Evidence(source="cost_explorer", observed_at=COLLECTED,
                          raw_ref=ref or f"ce#/{fid}", detail="test fact"))


def month(service: str, account: str, start: str, amount: str, **kw: Any) -> Any:
    from workload_graph.cost.periods import add_months

    first = date.fromisoformat(start)
    return fact("account_service_month", {"service": service, "linked_account": account},
                start, add_months(first, 1).isoformat(), amount, **kw)


def rkey(name: str, account: str, kind: str = "instance", region: str = "eu-west-1") -> Any:
    from workload_graph.ids import ResourceKey

    if kind == "bucket":
        return ResourceKey(account_id=account, region="global", arn=f"arn:aws:s3:::{name}")
    return ResourceKey(account_id=account, region=region,
                       arn=f"arn:aws:ec2:{region}:{account}:{kind}/{name}")


def workload(wid: str, members: list[tuple[Any, str]], confidence: str = "high",
             name: str | None = None) -> Any:
    from workload_graph.models import CandidateWorkload, Member

    return CandidateWorkload(
        workload_id=wid, name_hint=name or wid, confidence=confidence,
        accounts=sorted({k.account_id for k, _ in members}), sources_used=["tags"],
        shared_dependencies=[], external_refs=[], evidence_summary={},
        members=[Member(key=k, membership=m, reasons=[], evidence=[]) for k, m in members])


def build_result(workloads: list[Any], shared: list[Any] = (),  # type: ignore[assignment]
                 residual: list[Any] = (), unmapped: list[Any] = ()) -> Any:  # type: ignore[assignment]
    from workload_graph.models import (
        BuildResult,
        ProbeResult,
        ResidualGroup,
        SharedInfra,
        Unplaced,
    )

    groups = []
    for key in residual:
        groups.append(ResidualGroup(account_id=key.account_id, region=key.region,
                                    vpc_id=f"vpc-{key.arn[-4:]}",
                                    members=[Unplaced(key=key, reason="no_edges")]))
    return BuildResult(
        ruleset_version="0.1.0", ruleset_hash="h", workloads=workloads,
        shared_infrastructure=[SharedInfra(key=k, touches=["w1", "w2"]) for k in shared],
        residual_groups=groups,
        unmapped=[Unplaced(key=k, reason="no_edges") for k in unmapped],
        external_refs=[], probe=ProbeResult(entries=[], notes=[]), notes=[], generated_from=[])
