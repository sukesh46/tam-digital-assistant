"""F6 Cost Explorer and F7 Cost Optimization Hub collectors, with botocore Stubber. Offline."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber
from wg_cost_builders import AS_OF, COLLECTED, SERVICE_ACCOUNT, ce_page, group, result
from wg_test_helpers import ACCT_A, ACCT_B, ACCT_C

from workload_graph.collectors.base import (
    CallRecorder,
    CollectionScope,
    ForbiddenOperationError,
)
from workload_graph.collectors.cost_explorer import CostExplorerCollector
from workload_graph.collectors.cost_optimization_hub import CostOptimizationHubCollector
from workload_graph.cost.labels import LABELS_KEY
from workload_graph.cost.models import AccessPlan, CostScope
from workload_graph.findings.ruleset import FindingsRuleset

HISTORY = {"Start": "2026-08-01", "End": "2026-11-19"}
WINDOW = {"Start": "2026-11-06", "End": "2026-11-19"}
SA = [{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"}]
RA = [{"Type": "DIMENSION", "Key": "RESOURCE_ID"},
      {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"}]


def _client(service: str) -> Any:
    return boto3.client(service, region_name="us-east-1", aws_access_key_id="testing-not-a-key",
                        aws_secret_access_key="testing-not-a-secret")


def _scope(**overrides: Any) -> CostScope:
    args: dict[str, Any] = {"as_of": AS_OF, "lookback_months": 3, "metric": "UnblendedCost",
                            "max_calls": 40, "resource_level_window_days": 13,
                            "resource_level_top_services": 2, "tag_keys": ("App",)}
    args.update(overrides)
    return CostScope(**args)


def _linked(*accounts: str) -> dict[str, Any]:
    return {"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": list(accounts)}}


def _spend_params(linked: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"TimePeriod": HISTORY, "Granularity": "MONTHLY",
                              "Metrics": ["UnblendedCost"], "GroupBy": SA, "Filter": linked}
    if token:
        params["NextPageToken"] = token
    return params


def _window_params(linked: dict[str, Any]) -> dict[str, Any]:
    return {"TimePeriod": WINDOW, "Granularity": "DAILY", "Metrics": ["UnblendedCost"],
            "GroupBy": [{"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"}], "Filter": linked}


def _resource_params(linked: dict[str, Any], service: str) -> dict[str, Any]:
    return {"TimePeriod": WINDOW, "Granularity": "DAILY", "Metrics": ["UnblendedCost"],
            "GroupBy": RA, "Filter": {"And": [linked, {"Dimensions": {
                "Key": "SERVICE", "Values": [service]}}]}}


def _october(*rows: tuple[str, str]) -> dict[str, Any]:
    return ce_page([result("2026-10-01", "2026-11-01",
                           [group([s, ACCT_A], a) for s, a in rows])], SERVICE_ACCOUNT)


def _labels(snapshot: Any) -> dict[str, str]:
    return {e["label"]: e["status"] for e in snapshot.payload[LABELS_KEY]}


def _collect_ce(client: Any, plan: AccessPlan, scope: CostScope | None = None,
                sleeps: list[float] | None = None) -> Any:
    collector = CostExplorerCollector(sleep=(sleeps.append if sleeps is not None
                                             else lambda _s: None))
    return collector.collect(lambda *_a: client, plan, scope or _scope(), FindingsRuleset(),
                             COLLECTED)


# --- F6 --------------------------------------------------------------------------------------

def test_cost_explorer_makes_each_labelled_call_with_exact_parameters() -> None:
    client = _client("ce")
    linked = _linked(ACCT_A)
    with Stubber(client) as stub:
        stub.add_response("get_cost_and_usage", {**_october(("Amazon EC2", "500")),
                                                 "NextPageToken": "p2"}, _spend_params(linked))
        stub.add_response("get_cost_and_usage", {**_october(("Amazon RDS", "300")),
                                                 "NextPageToken": "p3"},
                          _spend_params(linked, "p2"))
        stub.add_response("get_cost_and_usage", _october(("Amazon S3", "20")),
                          _spend_params(linked, "p3"))
        stub.add_response("get_cost_and_usage", ce_page([], [("DIMENSION", "LINKED_ACCOUNT")]),
                          _window_params(linked))
        stub.add_response("get_tags", {"Tags": ["app", "team"], "ReturnSize": 2, "TotalSize": 2},
                          {"TimePeriod": HISTORY, "Filter": linked})
        stub.add_response("get_cost_and_usage", ce_page([], []), {
            "TimePeriod": HISTORY, "Granularity": "MONTHLY", "Metrics": ["UnblendedCost"],
            "GroupBy": [{"Type": "TAG", "Key": "app"}, SA[1]], "Filter": linked})
        stub.add_response("get_cost_and_usage_with_resources", ce_page([], []),
                          _resource_params(linked, "Amazon EC2"))
        stub.add_response("get_cost_and_usage_with_resources", ce_page([], []),
                          _resource_params(linked, "Amazon RDS"))
        snapshot = _collect_ce(client, AccessPlan(access_account_id=ACCT_A,
                                                  linked_account_ids=(ACCT_A,)))
        stub.assert_no_pending_responses()
    assert len(snapshot.payload["GetCostAndUsage:spend_by_service_account_monthly"]) == 3
    assert _labels(snapshot) == {
        "spend_by_service_account_monthly": "ok", "window_total_by_account": "ok",
        "tag_keys_seen": "ok", "spend_by_tag_monthly:app": "ok",
        "resource_costs_daily:Amazon EC2": "ok", "resource_costs_daily:Amazon RDS": "ok"}
    assert snapshot.status in {"ok", "empty"}
    assert snapshot.region == "us-east-1"


def test_payer_mode_filters_on_every_linked_account() -> None:
    client = _client("ce")
    linked = _linked(ACCT_A, ACCT_B, ACCT_C)
    with Stubber(client) as stub:
        stub.add_response("get_cost_and_usage", ce_page([], []), _spend_params(linked))
        stub.add_response("get_cost_and_usage", ce_page([], []), _window_params(linked))
        stub.add_response("get_tags", {"Tags": [], "ReturnSize": 0, "TotalSize": 0},
                          {"TimePeriod": HISTORY, "Filter": linked})
        snapshot = _collect_ce(client, AccessPlan(access_account_id=ACCT_C,
                                                  linked_account_ids=(ACCT_B, ACCT_A, ACCT_C)))
        stub.assert_no_pending_responses()
    assert snapshot.account_id == ACCT_C


def test_a_denied_label_is_recorded_and_resource_calls_are_skipped_with_a_note() -> None:
    client = _client("ce")
    linked = _linked(ACCT_A)
    with Stubber(client) as stub:
        stub.add_client_error("get_cost_and_usage", "AccessDeniedException", "no",
                              expected_params=_spend_params(linked))
        stub.add_response("get_tags", {"Tags": ["team"], "ReturnSize": 1, "TotalSize": 1},
                          {"TimePeriod": HISTORY, "Filter": linked})
        snapshot = _collect_ce(client, AccessPlan(access_account_id=ACCT_A,
                                                  linked_account_ids=(ACCT_A,)))
        stub.assert_no_pending_responses()
    labels = _labels(snapshot)
    assert labels["spend_by_service_account_monthly"] == "denied"
    # IAM denies per action, so the second GetCostAndUsage label is refused without a call.
    assert labels["window_total_by_account"] == "denied"
    assert labels["tag_keys_seen"] == "ok"
    assert any("spend by service is unavailable" in n for n in snapshot.notes)
    assert snapshot.status == "partial"


def test_resource_level_not_enabled_is_recorded_and_further_services_are_skipped() -> None:
    client = _client("ce")
    linked = _linked(ACCT_A)
    with Stubber(client) as stub:
        stub.add_response("get_cost_and_usage", _october(("Amazon EC2", "5"), ("Amazon S3", "3")),
                          _spend_params(linked))
        stub.add_response("get_cost_and_usage", ce_page([], []), _window_params(linked))
        stub.add_response("get_tags", {"Tags": [], "ReturnSize": 0, "TotalSize": 0},
                          {"TimePeriod": HISTORY, "Filter": linked})
        stub.add_client_error("get_cost_and_usage_with_resources", "DataUnavailableException",
                              "resource level data not enabled",
                              expected_params=_resource_params(linked, "Amazon EC2"))
        snapshot = _collect_ce(client, AccessPlan(access_account_id=ACCT_A,
                                                  linked_account_ids=(ACCT_A,)))
        stub.assert_no_pending_responses()
    labels = _labels(snapshot)
    assert labels["resource_costs_daily:Amazon EC2"] == "not_enabled"
    assert labels["resource_costs_daily:Amazon S3"] == "skipped"


def test_throttling_retries_count_against_the_budget_and_exhaustion_is_partial() -> None:
    client = _client("ce")
    linked = _linked(ACCT_A)
    sleeps: list[float] = []
    with Stubber(client) as stub:
        stub.add_client_error("get_cost_and_usage", "ThrottlingException", "slow",
                              expected_params=_spend_params(linked))
        stub.add_response("get_cost_and_usage", _october(("Amazon EC2", "5")),
                          _spend_params(linked))
        stub.add_client_error("get_cost_and_usage", "ThrottlingException", "slow",
                              expected_params=_window_params(linked))
        snapshot = _collect_ce(client, AccessPlan(access_account_id=ACCT_A,
                                                  linked_account_ids=(ACCT_A,)),
                               _scope(max_calls=3), sleeps)
        stub.assert_no_pending_responses()
    labels = _labels(snapshot)
    assert labels["spend_by_service_account_monthly"] == "ok"
    assert labels["window_total_by_account"] == "error"
    assert labels["tag_keys_seen"] == "skipped"
    assert len(sleeps) == 2
    assert snapshot.status == "partial"


def test_cost_explorer_refuses_operations_outside_its_allowlist() -> None:
    recorder = CallRecorder(CollectionScope(now=COLLECTED), lambda _s: None,
                            CostExplorerCollector.allowed_operations)
    for operation in ("GetCostForecast", "UpdateCostAllocationTagsStatus", "DeleteAnomalyMonitor",
                      "CreateCostCategoryDefinition"):
        with pytest.raises(ForbiddenOperationError):
            recorder.call(_client("ce"), operation)


# --- F7 --------------------------------------------------------------------------------------

def _rec(rec_id: str, savings: float) -> dict[str, Any]:
    return {"recommendationId": rec_id, "accountId": ACCT_A, "region": "eu-west-1",
            "resourceArn": f"arn:aws:ec2:eu-west-1:{ACCT_A}:instance/{rec_id}",
            "currentResourceType": "Ec2Instance", "actionType": "Rightsize",
            "estimatedMonthlySavings": savings, "currencyCode": "USD",
            "lastRefreshTimestamp": datetime(2026, 11, 18, tzinfo=UTC)}


def _collect_coh(client: Any, plan: AccessPlan, scope: CostScope | None = None) -> Any:
    return CostOptimizationHubCollector(sleep=lambda _s: None).collect(
        lambda *_a: client, plan, scope or _scope(), FindingsRuleset(), COLLECTED)


def test_hub_lists_enrolment_recommendations_and_one_summary_per_account() -> None:
    client = _client("cost-optimization-hub")
    with Stubber(client) as stub:
        stub.add_response("list_enrollment_statuses", {"items": []}, {})
        stub.add_response("list_recommendations", {"items": [_rec("r1", 12.3)],
                                                   "nextToken": "n2"},
                          {"filter": {"accountIds": [ACCT_A, ACCT_B]},
                           "includeAllRecommendations": False})
        stub.add_response("list_recommendations", {"items": [_rec("r2", 0.1)]},
                          {"filter": {"accountIds": [ACCT_A, ACCT_B]},
                           "includeAllRecommendations": False, "nextToken": "n2"})
        for account in (ACCT_A, ACCT_B):
            stub.add_response("list_recommendation_summaries",
                              {"items": [], "estimatedTotalDedupedSavings": 10.5,
                               "currencyCode": "USD"},
                              {"groupBy": "ActionType", "filter": {"accountIds": [account]}})
        snapshot = _collect_coh(client, AccessPlan(access_account_id=ACCT_A,
                                                   linked_account_ids=(ACCT_A, ACCT_B)))
        stub.assert_no_pending_responses()
    pages = snapshot.payload["ListRecommendations:list_recommendations"]
    assert pages[1]["items"][0]["estimatedMonthlySavings"] == Decimal("0.1")
    assert isinstance(pages[0]["items"][0]["estimatedMonthlySavings"], Decimal)
    assert set(_labels(snapshot).values()) == {"ok"}


def test_hub_skips_enrolment_when_the_model_lacks_it() -> None:
    real = _client("cost-optimization-hub")

    class NoEnrolment:
        meta = SimpleNamespace(
            service_model=SimpleNamespace(operation_names=["ListRecommendations"]),
            method_to_api_mapping=real.meta.method_to_api_mapping,
            endpoint_url=real.meta.endpoint_url)

        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

    with Stubber(real) as stub:
        stub.add_response("list_recommendations", {"items": []},
                          {"filter": {"accountIds": [ACCT_A]},
                           "includeAllRecommendations": False})
        stub.add_response("list_recommendation_summaries", {"items": []},
                          {"groupBy": "ActionType", "filter": {"accountIds": [ACCT_A]}})
        snapshot = _collect_coh(NoEnrolment(), AccessPlan(access_account_id=ACCT_A,
                                                          linked_account_ids=(ACCT_A,)))
        stub.assert_no_pending_responses()
    assert _labels(snapshot)["enrolment"] == "skipped"
    assert any("not in this botocore model" in n for n in snapshot.notes)


def test_hub_not_opted_in_maps_to_not_enabled() -> None:
    client = _client("cost-optimization-hub")
    with Stubber(client) as stub:
        stub.add_response("list_enrollment_statuses", {"items": []}, {})
        stub.add_client_error("list_recommendations", "AccessDeniedException",
                              "account is not enrolled in Cost Optimization Hub")
        stub.add_client_error("list_recommendation_summaries", "AccessDeniedException",
                              "account is not enrolled in Cost Optimization Hub")
        snapshot = _collect_coh(client, AccessPlan(access_account_id=ACCT_A,
                                                   linked_account_ids=(ACCT_A,)))
    labels = _labels(snapshot)
    assert labels["list_recommendations"] == "not_enabled"
    assert labels[f"summary_account:{ACCT_A}"] == "not_enabled"


def test_hub_refuses_update_operations_explicitly() -> None:
    allowed = CostOptimizationHubCollector.allowed_operations
    assert "UpdateEnrollmentStatus" not in allowed and "UpdatePreferences" not in allowed
    recorder = CallRecorder(CollectionScope(now=COLLECTED), lambda _s: None, allowed)
    for operation in ("UpdateEnrollmentStatus", "UpdatePreferences"):
        with pytest.raises(ForbiddenOperationError):
            recorder.call(_client("cost-optimization-hub"), operation)
    # Even if someone added them to the allowlist, the forbidden prefix still refuses them.
    widened = CallRecorder(CollectionScope(now=COLLECTED), lambda _s: None,
                           allowed | {"UpdateEnrollmentStatus"})
    with pytest.raises(ForbiddenOperationError):
        widened.call(_client("cost-optimization-hub"), "UpdateEnrollmentStatus")
