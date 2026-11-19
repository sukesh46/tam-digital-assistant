"""Cost scenarios C1-C18 (docs/COST_SCENARIOS.md) and the F16 property invariants. Offline.

Scenarios already pinned by a focused unit test name it in the docs table; the rest are here.
"""

from __future__ import annotations

import random
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st
from wg_cost_builders import (
    AS_OF,
    SERVICE_ACCOUNT,
    build_result,
    ce_page,
    cost_snapshot,
    fact,
    group,
    result,
    rkey,
    scenario,
    workload,
)
from wg_test_helpers import ACCT_A, ACCT_B

from workload_graph.cost.labels import SPEND_BY_SERVICE, TAG_KEYS_SEEN, WINDOW_TOTAL, payload_key
from workload_graph.findings.attribution import attribute
from workload_graph.findings.drivers import spend_findings
from workload_graph.findings.index import ResourceIndex
from workload_graph.findings.models import Confidence, FindingKind
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.findings.savings import savings_findings
from workload_graph.normalisers.cost_explorer import normalise_cost_explorer
from workload_graph.normalisers.cost_optimization_hub import normalise_cost_optimization_hub
from workload_graph.pipeline_findings import run
from workload_graph.report.assemble import build_result_sha256


def test_c1_tagged_workload_with_matching_recommendations() -> None:
    built, cost, graph = scenario(ACCT_A)
    (orders,) = run(built, cost, as_of=AS_OF, graph_snapshots=graph).workloads
    assert [s.method for s in orders.spend if s.primary] == ["tag"]
    assert orders.savings_sum_of_resource_recommendations == "120"


def test_c2_resource_level_entry_with_explained_ratio() -> None:
    built, cost, graph = scenario(ACCT_A)
    (orders,) = run(built, cost, as_of=AS_OF, graph_snapshots=graph).workloads
    level = [s for s in orders.spend if s.method == "resource_level"][0]
    assert level.amount == Decimal(40)
    assert level.explained_ratio == Decimal(55) / Decimal(130)


def test_c3_no_tags_no_resource_level_two_workloads() -> None:
    built, cost, graph = scenario(ACCT_A, tagged=False, resource_level=False)
    two = build_result([*built.workloads, workload("w2", [(rkey("i-w2", ACCT_A), "declared")])])
    report = run(two, cost, as_of=AS_OF, graph_snapshots=graph)
    assert {s.method for w in report.workloads for s in w.spend} == {"none"}
    assert report.workloads[0].savings_sum_of_resource_recommendations == "120"


def test_c4_sole_workload_account_is_low_confidence() -> None:
    _, cost, graph = scenario(ACCT_A, tagged=False, resource_level=False)
    alone = build_result([workload("w1", [(rkey("i-w1", ACCT_A), "declared")], name="orders")])
    (orders,) = run(alone, cost, as_of=AS_OF, graph_snapshots=graph).workloads
    sole = [s for s in orders.spend if s.primary][0]
    assert (sole.method, sole.confidence) == ("sole_account", Confidence.LOW)
    assert sole.notes


def test_c6_cost_explorer_denied_keeps_savings() -> None:
    built, cost, graph = scenario(ACCT_A)
    denied = cost_snapshot("cost_explorer", ACCT_A, {}, snapshot_id="ce-denied", statuses={
        SPEND_BY_SERVICE: ("GetCostAndUsage", "denied", "AccessDeniedException"),
        WINDOW_TOTAL: ("GetCostAndUsage", "denied", "AccessDeniedException"),
        TAG_KEYS_SEEN: ("GetTags", "denied", "AccessDeniedException")})
    report = run(built, [denied, cost[1]], as_of=AS_OF, graph_snapshots=graph)
    assert not [f for f in report.findings if f.kind is FindingKind.SPEND_CHANGE]
    ce = [e for e in report.coverage.entries if e.source == "cost_explorer"][0]
    assert ce.status == "denied"
    assert "A-ACCESS:cost_explorer" in {a.ask_id for a in report.customer_asks}
    assert [f for f in report.findings if f.kind is FindingKind.SAVINGS_OPPORTUNITY]


def test_c17_multi_page_responses_are_merged() -> None:
    built, cost, graph = scenario(ACCT_A)
    single = run(built, cost, as_of=AS_OF, graph_snapshots=graph)
    key = payload_key("GetCostAndUsage", SPEND_BY_SERVICE)
    (page,) = cost[0].payload[key]
    results = page["ResultsByTime"]
    split = [ce_page(results[:2], SERVICE_ACCOUNT, token="t1"), ce_page(results[2:],
                                                                         SERVICE_ACCOUNT)]
    paged = cost[0].model_copy(update={"payload": {**cost[0].payload, key: split}})
    report = run(built, [paged, cost[1]], as_of=AS_OF, graph_snapshots=graph)
    pick = [f.values for f in report.findings if f.rule_id == "spend_change"]
    assert pick == [f.values for f in single.findings if f.rule_id == "spend_change"]


def test_build_result_hash_ignores_list_order_but_not_content() -> None:
    built, _, _ = scenario(ACCT_A)
    two = build_result([*built.workloads, workload("w2", [(rkey("i-w2", ACCT_A), "declared")])])
    swapped = two.model_copy(update={"workloads": list(reversed(two.workloads))})
    assert build_result_sha256(two) == build_result_sha256(swapped)
    assert build_result_sha256(two) != build_result_sha256(built)


# --- properties -------------------------------------------------------------------------------

_PLACES = ["i-w1", "lb", "i-res", "i-ghost", "NoResourceId"]


@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(st.sampled_from(_PLACES), st.integers(0, 10_000), st.integers(0, 99)),
                max_size=12),
       st.integers(0, 50_000))
def test_buckets_always_reconcile_exactly(rows: list[tuple[str, int, int]], slack: int) -> None:
    built, _, _ = scenario(ACCT_A)
    facts = []
    spent = Decimal(0)
    for day, (place, whole, cents) in enumerate(rows):
        amount = Decimal(whole) + Decimal(cents) / 100
        spent += amount
        resource = rkey("lb", ACCT_A, kind="loadbalancer").arn if place == "lb" else place
        start = f"2026-11-{6 + day % 12:02d}"
        facts.append(fact("resource_day", {"resource_id": resource, "linked_account": ACCT_A,
                                           "service": "Amazon EC2"}, start,
                          f"2026-11-{7 + day % 12:02d}", str(amount), ref=f"ce#/r/{day}"))
    total = spent + slack
    facts.append(fact("account_window_total", {"linked_account": ACCT_A}, "2026-11-06",
                      "2026-11-19", str(total)))
    attribution = attribute(facts, ResourceIndex(built), FindingsRuleset(), ["app"], AS_OF)
    for table in attribution.buckets:
        assert sum(table.rows.values(), Decimal(0)) == table.total == total


@settings(max_examples=15, deadline=None)
@given(st.randoms(use_true_random=False))
def test_any_input_order_gives_the_same_bytes(rng: random.Random) -> None:
    """Order of snapshots, workloads, facts and recommendations; raw responses are untouched
    (moving an item inside a response rightly moves its evidence pointer)."""
    built, cost, graph = scenario(ACCT_A)
    _, b_cost, _ = scenario(ACCT_B)
    two = build_result([*built.workloads, workload("w2", [(rkey("i-w2", ACCT_A), "declared")])],
                       shared=[rkey("lb", ACCT_A, kind="loadbalancer")],
                       residual=[rkey("i-res", ACCT_A)])
    everything = cost + b_cost
    expected = run(two, everything, as_of=AS_OF, graph_snapshots=graph).to_json()
    swapped = two.model_copy(update={"workloads": rng.sample(two.workloads, 2)})
    shuffled = rng.sample(everything, len(everything))
    assert run(swapped, shuffled, as_of=AS_OF, graph_snapshots=graph).to_json() == expected

    facts = [f for s in everything if s.source == "cost_explorer"
             for f in normalise_cost_explorer(s, "UnblendedCost").facts]
    rules = FindingsRuleset()
    accounts = [ACCT_A, ACCT_B]
    assert spend_findings(rng.sample(facts, len(facts)), accounts, AS_OF, rules) == \
        spend_findings(facts, accounts, AS_OF, rules)
    index = ResourceIndex(two)
    assert attribute(rng.sample(facts, len(facts)), index, rules, ["app"], AS_OF) == \
        attribute(facts, index, rules, ["app"], AS_OF)
    recs = [r for s in everything if s.source == "cost_optimization_hub"
            for r in normalise_cost_optimization_hub(s).recommendations]
    assert savings_findings(rng.sample(recs, len(recs)), [], index, rules, AS_OF, "USD", {}) \
        == savings_findings(recs, [], index, rules, AS_OF, "USD", {})


def test_currency_and_groups_builders_stay_fake() -> None:
    assert group(["x", ACCT_A], "1")["Metrics"]["UnblendedCost"]["Unit"] == "USD"
    assert result("2026-10-01", "2026-11-01", [])["Estimated"] is False
