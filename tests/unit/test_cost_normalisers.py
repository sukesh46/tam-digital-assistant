"""F4 Cost Explorer and F5 Cost Optimization Hub normalisers. Pure and offline."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from wg_cost_builders import (
    ACCOUNT_ONLY,
    RESOURCE_ACCOUNT,
    SERVICE_ACCOUNT,
    ce_page,
    cost_snapshot,
    group,
    recommendation,
    result,
    summary,
)
from wg_test_helpers import ACCT_A, ACCT_B

from workload_graph.cost.labels import (
    LIST_RECOMMENDATIONS,
    SPEND_BY_SERVICE,
    SUMMARY_PREFIX,
    TAG_KEYS_SEEN,
    TAG_SPEND_PREFIX,
    WINDOW_TOTAL,
)
from workload_graph.cost.snapshots import resolve_ref
from workload_graph.normalisers.cost_explorer import normalise_cost_explorer, split_tag_group_key
from workload_graph.normalisers.cost_optimization_hub import normalise_cost_optimization_hub

GCU = "GetCostAndUsage"


def _monthly(defs: list[tuple[str, str]] = SERVICE_ACCOUNT) -> list[dict[str, object]]:
    first = ["Amazon EC2", ACCT_A] if defs == SERVICE_ACCOUNT else [ACCT_A, "Amazon EC2"]
    second = ["Amazon S3", ACCT_A] if defs == SERVICE_ACCOUNT else [ACCT_A, "Amazon S3"]
    return [ce_page([
        result("2026-09-01", "2026-10-01", [group(first, "100.10"), group(second, "5")]),
        result("2026-10-01", "2026-11-01", [group(first, "130.25")]),
        result("2026-11-01", "2026-11-19", [group(first, "70")], estimated=True),
    ], defs)]


def test_monthly_groups_become_facts_with_dimensions_from_group_definitions() -> None:
    snap = cost_snapshot("cost_explorer", ACCT_A, {(GCU, SPEND_BY_SERVICE): _monthly()})
    batch = normalise_cost_explorer(snap)
    facts = [f for f in batch.facts if f.kind == "account_service_month"]
    assert len(facts) == 4
    october = [f for f in facts if f.period.start.isoformat() == "2026-10-01"][0]
    assert october.dimensions == {"linked_account": ACCT_A, "service": "Amazon EC2"}
    assert october.amount == Decimal("130.25") and october.unit == "USD"
    assert not october.estimated
    assert [f for f in facts if f.estimated][0].period.end.isoformat() == "2026-11-19"


def test_group_definitions_in_the_opposite_order_still_map_correctly() -> None:
    snap = cost_snapshot("cost_explorer", ACCT_A,
                         {(GCU, SPEND_BY_SERVICE): _monthly(list(reversed(SERVICE_ACCOUNT)))})
    facts = normalise_cost_explorer(snap).facts
    assert {f.dimensions["service"] for f in facts} == {"Amazon EC2", "Amazon S3"}
    assert {f.dimensions["linked_account"] for f in facts} == {ACCT_A}


def test_tag_group_keys_split_on_the_first_dollar() -> None:
    assert split_tag_group_key("app$orders") == ("app", "orders")
    assert split_tag_group_key("app$") == ("app", "")
    assert split_tag_group_key("app$a$b") == ("app", "a$b")
    assert split_tag_group_key("noseparator") == ("noseparator", "")


def test_tag_spend_becomes_tag_month_facts_including_untagged() -> None:
    page = ce_page([result("2026-10-01", "2026-11-01", [
        group(["app$orders", ACCT_A], "40"), group(["app$", ACCT_A], "60")])],
        [("TAG", "app"), ("DIMENSION", "LINKED_ACCOUNT")])
    snap = cost_snapshot("cost_explorer", ACCT_A, {(GCU, TAG_SPEND_PREFIX + "app"): [page]})
    facts = sorted(normalise_cost_explorer(snap).facts, key=lambda f: f.dimensions["tag_value"])
    assert [(f.kind, f.dimensions["tag_key"], f.dimensions["tag_value"], f.amount)
            for f in facts] == [("tag_month", "app", "", Decimal(60)),
                                ("tag_month", "app", "orders", Decimal(40))]


def test_daily_window_calls_produce_per_account_window_totals() -> None:
    page = ce_page([
        result("2026-11-06", "2026-11-07", [group([ACCT_A], "1.10"), group([ACCT_B], "2")]),
        result("2026-11-07", "2026-11-08", [group([ACCT_A], "1.15")]),
    ], ACCOUNT_ONLY)
    snap = cost_snapshot("cost_explorer", ACCT_A, {(GCU, WINDOW_TOTAL): [page]})
    totals = {f.dimensions["linked_account"]: f for f in normalise_cost_explorer(snap).facts
              if f.kind == "account_window_total"}
    assert totals[ACCT_A].amount == Decimal("2.25")
    assert totals[ACCT_B].amount == Decimal(2)
    assert (totals[ACCT_A].period.start.isoformat(), totals[ACCT_A].period.end.isoformat()) == \
        ("2026-11-06", "2026-11-08")


def test_resource_costs_become_resource_day_facts() -> None:
    page = ce_page([result("2026-11-06", "2026-11-07", [
        group(["i-0abc", ACCT_A], "3"), group(["NoResourceId", ACCT_A], "1")])],
        RESOURCE_ACCOUNT)
    snap = cost_snapshot("cost_explorer", ACCT_A, {
        ("GetCostAndUsageWithResources", "resource_costs_daily:Amazon EC2"): [page]})
    facts = normalise_cost_explorer(snap).facts
    assert {(f.dimensions["resource_id"], f.dimensions["service"]) for f in facts} == {
        ("i-0abc", "Amazon EC2"), ("NoResourceId", "Amazon EC2")}


def test_a_group_without_the_metric_is_skipped_with_a_note() -> None:
    page = ce_page([result("2026-10-01", "2026-11-01", [
        {"Keys": ["Amazon EC2", ACCT_A], "Metrics": {}}, group(["Amazon S3", ACCT_A], "1")])],
        SERVICE_ACCOUNT)
    snap = cost_snapshot("cost_explorer", ACCT_A, {(GCU, SPEND_BY_SERVICE): [page]})
    batch = normalise_cost_explorer(snap)
    assert len(batch.facts) == 1
    assert any("no amount" in note for note in batch.notes)


def test_malformed_rows_are_skipped_with_a_note_not_an_exception() -> None:
    page = {"GroupDefinitions": [{"Type": "DIMENSION", "Key": "SERVICE"}],
            "ResultsByTime": [{"TimePeriod": {"Start": "bad"}, "Groups": [group(["x"], "1")]},
                              "not a dict"]}
    snap = cost_snapshot("cost_explorer", ACCT_A, {(GCU, SPEND_BY_SERVICE): [page]})
    batch = normalise_cost_explorer(snap)
    assert batch.facts == ()
    assert batch.notes


def test_evidence_pointers_resolve_to_the_exact_group() -> None:
    snap = cost_snapshot("cost_explorer", ACCT_A, {(GCU, SPEND_BY_SERVICE): _monthly()})
    for fact in normalise_cost_explorer(snap).facts:
        datum = resolve_ref({snap.snapshot_id: snap}, fact.evidence.raw_ref)
        assert Decimal(datum["Metrics"]["UnblendedCost"]["Amount"]) == fact.amount


def test_output_is_sorted_and_stable_when_pages_are_reordered() -> None:
    pages = [ce_page([result("2026-09-01", "2026-10-01", [group(["A", ACCT_A], "1")])],
                     SERVICE_ACCOUNT, token="t"),
             ce_page([result("2026-10-01", "2026-11-01", [group(["B", ACCT_A], "2")])],
                     SERVICE_ACCOUNT)]
    one = normalise_cost_explorer(cost_snapshot("cost_explorer", ACCT_A,
                                                {(GCU, SPEND_BY_SERVICE): pages}))
    two = normalise_cost_explorer(cost_snapshot("cost_explorer", ACCT_A,
                                                {(GCU, SPEND_BY_SERVICE): pages[::-1]}))
    assert [(f.fact_id, f.amount) for f in one.facts] == [(f.fact_id, f.amount) for f in two.facts]
    assert list(one.facts) == sorted(one.facts, key=lambda f: f.sort_key())


def test_tag_keys_seen_and_label_status_are_reported() -> None:
    snap = cost_snapshot("cost_explorer", ACCT_A, {
        ("GetTags", TAG_KEYS_SEEN): [{"Tags": ["app", "team"], "ReturnSize": 2, "TotalSize": 2}]},
        statuses={"resource_costs_daily:Amazon EC2": (
            "GetCostAndUsageWithResources", "not_enabled", "DataUnavailableException")})
    batch = normalise_cost_explorer(snap)
    assert batch.tag_keys_seen == ("app", "team")
    assert batch.label_status["resource_costs_daily:Amazon EC2"] == "not_enabled"
    assert batch.label_status[TAG_KEYS_SEEN] == "ok"


# --- F5 -------------------------------------------------------------------------------------

def _coh(items: list[dict[str, object]], summaries: dict[str, object] | None = None) -> object:
    calls: dict[tuple[str, str], list[dict[str, object]]] = {
        ("ListRecommendations", LIST_RECOMMENDATIONS): [{"items": items}]}
    for account, total in (summaries or {}).items():
        calls[("ListRecommendationSummaries", SUMMARY_PREFIX + account)] = [summary(total)]
    return cost_snapshot("cost_optimization_hub", ACCT_A, calls)  # type: ignore[arg-type]


def test_a_full_recommendation_maps_every_field() -> None:
    arn = f"arn:aws:ec2:eu-west-1:{ACCT_A}:instance/i-0abc"
    snap = _coh([recommendation("r1", ACCT_A, arn=arn, resource_id="i-0abc", savings=12.3)])
    batch = normalise_cost_optimization_hub(snap)  # type: ignore[arg-type]
    rec = batch.recommendations[0]
    assert rec.recommendation_id == "r1" and rec.account_id == ACCT_A
    assert rec.resource_arn == arn and rec.resource_id == "i-0abc"
    assert rec.estimated_monthly_savings == Decimal("12.3")
    assert rec.estimated_monthly_cost == Decimal("200.0")
    assert rec.implementation_effort == "Low"
    assert rec.restart_needed is False and rec.rollback_possible is True
    assert rec.last_refresh == datetime(2026, 11, 18, tzinfo=UTC)
    assert rec.currency == "USD" and rec.action_type == "Rightsize"


def test_absent_optional_fields_stay_none() -> None:
    snap = _coh([recommendation("r2", ACCT_A, cost=None, effort=None, restart=None,
                                rollback=None, refreshed=None)])
    rec = normalise_cost_optimization_hub(snap).recommendations[0]  # type: ignore[arg-type]
    assert rec.estimated_monthly_cost is None
    assert rec.implementation_effort is None
    assert rec.restart_needed is None and rec.rollback_possible is None
    assert rec.last_refresh is None
    assert rec.resource_arn is None and rec.resource_id is None


def test_float_inputs_convert_through_str() -> None:
    item = recommendation("r3", ACCT_A)
    item["estimatedMonthlySavings"] = 0.1
    rec = normalise_cost_optimization_hub(_coh([item])).recommendations[0]  # type: ignore[arg-type]
    assert rec.estimated_monthly_savings == Decimal("0.1")


def test_summaries_yield_deduplicated_totals_and_a_missing_total_is_a_note() -> None:
    snap = _coh([], {ACCT_A: "80.5", ACCT_B: None})
    batch = normalise_cost_optimization_hub(snap)  # type: ignore[arg-type]
    assert [(s.account_id, s.deduped_total, s.currency) for s in batch.summaries] == [
        (ACCT_A, Decimal("80.5"), "USD")]
    assert any(ACCT_B in note for note in batch.notes)


def test_recommendation_pointers_resolve() -> None:
    snap = _coh([recommendation("r1", ACCT_A), recommendation("r2", ACCT_A)])
    for rec in normalise_cost_optimization_hub(snap).recommendations:  # type: ignore[arg-type]
        datum = resolve_ref({snap.snapshot_id: snap}, rec.evidence.raw_ref)  # type: ignore[attr-defined]
        assert datum["recommendationId"] == rec.recommendation_id


def test_a_malformed_recommendation_is_skipped_with_a_note() -> None:
    snap = _coh([{"recommendationId": "bad"}, recommendation("ok", ACCT_A)])
    batch = normalise_cost_optimization_hub(snap)  # type: ignore[arg-type]
    assert [r.recommendation_id for r in batch.recommendations] == ["ok"]
    assert batch.notes
