"""F10 attribution. Hand-computed numbers; exact Decimal assertions. Offline."""

from __future__ import annotations

from decimal import Decimal

from wg_cost_builders import AS_OF, build_result, fact, rkey, workload
from wg_test_helpers import ACCT_A, ACCT_B

from workload_graph.findings.attribution import attribute
from workload_graph.findings.index import ResourceIndex
from workload_graph.findings.models import Confidence
from workload_graph.findings.ruleset import FindingsRuleset

TAG_KEYS = ["app", "project"]
W_START, W_END = "2026-11-06", "2026-11-19"

I_W1 = rkey("i-w1", ACCT_A)
I_W1B = rkey("i-w1b", ACCT_A)
I_W2A = rkey("i-w2a", ACCT_A)
I_W2B = rkey("i-w2b", ACCT_B)
LB = rkey("lb-shared", ACCT_A, kind="loadbalancer")
RES = rkey("i-res", ACCT_A)


def _day(resource: str, account: str, amount: str, day: str = "2026-11-06",
         unit: str = "USD") -> object:
    end = f"2026-11-{int(day[-2:]) + 1:02d}"
    return fact("resource_day", {"resource_id": resource, "linked_account": account,
                                 "service": "Amazon EC2"}, day, end, amount, unit=unit)


def _window(account: str, amount: str) -> object:
    return fact("account_window_total", {"linked_account": account}, W_START, W_END, amount)


def _tag(value: str, account: str, start: str, end: str, amount: str, key: str = "app") -> object:
    return fact("tag_month", {"tag_key": key, "tag_value": value, "linked_account": account},
                start, end, amount)


def _index(result: object, tags: dict[str, dict[str, str]] | None = None) -> ResourceIndex:
    return ResourceIndex(result, tags=tags)  # type: ignore[arg-type]


def _two_workloads() -> object:
    return build_result([
        workload("w1", [(I_W1, "declared"), (I_W1B, "attached")]),
        workload("w2", [(I_W2A, "declared"), (I_W2B, "declared")], confidence="medium"),
    ], shared=[LB], residual=[RES])


# --- method A ---------------------------------------------------------------------------------

def test_resource_level_buckets_reconcile_exactly_to_the_window_total() -> None:
    facts = [_window(ACCT_A, "100"), _day("i-w1", ACCT_A, "40"), _day("lb-shared", ACCT_A, "10"),
             _day("i-res", ACCT_A, "5"), _day("i-zzz", ACCT_A, "3"),
             _day("NoResourceId", ACCT_A, "2"), _day("i-w2a", ACCT_A, "7.25")]
    result = attribute(facts, _index(_two_workloads()), FindingsRuleset(), TAG_KEYS, AS_OF)
    table = result.buckets[0]
    assert table.rows == {
        "no_resource_id": Decimal(2), "shared": Decimal(10), "unexplained": Decimal("32.75"),
        "unknown": Decimal(3), "unplaced": Decimal(5), "workload:w1": Decimal(40),
        "workload:w2": Decimal("7.25")}
    assert sum(table.rows.values()) == table.total == Decimal(100)
    assert table.explained_ratio == Decimal("0.6725")


def test_a_workload_spanning_two_accounts_sums_both() -> None:
    facts = [_window(ACCT_A, "50"), _window(ACCT_B, "30"), _day("i-w2a", ACCT_A, "20"),
             _day("i-w2b", ACCT_B, "25")]
    result = attribute(facts, _index(_two_workloads()), FindingsRuleset(), TAG_KEYS, AS_OF)
    w2 = [s for s in result.spend if s.workload_id == "w2" and s.method == "resource_level"][0]
    assert w2.amount == Decimal(45)
    assert w2.explained_ratio == Decimal("0.4")  # min of 20/50 and 25/30
    assert w2.confidence is Confidence.LOW  # capped: explained ratio under 0.5


def test_resource_level_confidence_is_the_minimum_of_workload_and_method() -> None:
    facts = [_window(ACCT_A, "50"), _day("i-w1", ACCT_A, "45")]
    result = attribute(facts, _index(_two_workloads()), FindingsRuleset(), TAG_KEYS, AS_OF)
    w1 = [s for s in result.spend if s.workload_id == "w1"][0]
    assert w1.method == "resource_level" and w1.primary
    assert w1.confidence is Confidence.HIGH


def test_low_explained_ratio_and_high_unplaced_share_raise_findings() -> None:
    facts = [_window(ACCT_A, "100"), _day("i-w1", ACCT_A, "10"), _day("lb-shared", ACCT_A, "30")]
    result = attribute(facts, _index(_two_workloads()), FindingsRuleset(), TAG_KEYS, AS_OF)
    rules = sorted(f.rule_id for f in result.findings)
    assert rules == ["attribution_low_explained_ratio", "unplaced_share_high"]
    share = [f for f in result.findings if f.rule_id == "unplaced_share_high"][0]
    assert share.values["share"] == "0.9" and share.values["amount"] == "90"


# --- method B ---------------------------------------------------------------------------------

def _tagged(tags: dict[str, dict[str, str]]) -> ResourceIndex:
    return _index(build_result([workload("w1", [(I_W1, "declared"), (I_W1B, "declared")])]),
                  tags={k.key_str(): v for k, v in
                        ((I_W1, tags.get("a", {})), (I_W1B, tags.get("b", {})))})


TAG_MONTHS = [("2026-08-01", "2026-09-01", "10"), ("2026-09-01", "2026-10-01", "20"),
              ("2026-10-01", "2026-11-01", "30")]


def test_tag_method_resolves_a_unique_pair_and_keeps_the_monthly_series() -> None:
    index = _tagged({"a": {"App": "orders"}, "b": {"app": "orders"}})
    facts = [_tag("orders", ACCT_A, s, e, a, key="App") for s, e, a in TAG_MONTHS]
    facts.append(_tag("other", ACCT_A, "2026-10-01", "2026-11-01", "99", key="App"))
    result = attribute(facts, index, FindingsRuleset(), TAG_KEYS, AS_OF)
    entry = result.spend[0]
    assert (entry.method, entry.primary, entry.amount) == ("tag", True, Decimal(30))
    assert [m.amount for m in entry.series] == [Decimal(10), Decimal(20), Decimal(30)]
    assert entry.confidence is Confidence.MEDIUM  # min(high, tag=medium)
    assert result.tag_pairs == {"w1": ("app", "orders")}
    assert "outside the workload map" in entry.notes[0]


def test_tag_method_is_skipped_when_two_keys_qualify() -> None:
    index = _tagged({"a": {"app": "orders", "project": "x"}, "b": {"app": "orders",
                                                                  "project": "x"}})
    result = attribute([_tag("orders", ACCT_A, *TAG_MONTHS[2])], index, FindingsRuleset(),
                       TAG_KEYS, AS_OF)
    assert result.spend[0].method == "none"
    assert any("tag_ambiguous" in n for n in result.notes)


def test_tag_method_is_skipped_when_members_carry_two_values() -> None:
    index = _tagged({"a": {"app": "orders"}, "b": {"app": "billing"}})
    result = attribute([], index, FindingsRuleset(), TAG_KEYS, AS_OF)
    assert any("tag_ambiguous" in n for n in result.notes)


def test_tag_method_is_skipped_when_the_member_share_is_under_the_threshold() -> None:
    index = _tagged({"a": {"app": "orders"}, "b": {}})
    result = attribute([_tag("orders", ACCT_A, *TAG_MONTHS[2])], index, FindingsRuleset(),
                       TAG_KEYS, AS_OF)
    assert result.spend[0].method == "none"
    assert any("tag_unavailable" in n for n in result.notes)
    loose = attribute([_tag("orders", ACCT_A, *TAG_MONTHS[2])], index,
                      FindingsRuleset(tag_min_member_share=Decimal("0.5")), TAG_KEYS, AS_OF)
    assert loose.spend[0].method == "tag"


# --- method C and primary selection -----------------------------------------------------------

def _sole_result(extra_unplaced: int = 0) -> object:
    others = [rkey(f"i-u{n}", ACCT_B) for n in range(extra_unplaced)]
    return build_result([workload("w9", [(rkey(f"i-m{n}", ACCT_B), "declared")
                                         for n in range(8)], confidence="high")],
                        unmapped=others)


def _october(account: str, amount: str) -> object:
    return fact("account_service_month", {"service": "Amazon EC2", "linked_account": account},
                "2026-10-01", "2026-11-01", amount)


def test_sole_workload_account_applies_within_the_unplaced_limit() -> None:
    result = attribute([_october(ACCT_B, "500")], _index(_sole_result(2)), FindingsRuleset(),
                       TAG_KEYS, AS_OF)
    entry = result.spend[0]
    assert (entry.method, entry.amount, entry.confidence) == (
        "sole_account", Decimal(500), Confidence.LOW)
    assert "whole account" in entry.notes[0]


def test_sole_workload_account_is_refused_above_the_unplaced_limit() -> None:
    result = attribute([_october(ACCT_B, "500")], _index(_sole_result(3)), FindingsRuleset(),
                       TAG_KEYS, AS_OF)
    assert result.spend[0].method == "none"  # 3 of 11 resources outside > 0.2


def test_sole_workload_account_needs_no_other_workload_in_the_account() -> None:
    result = attribute([_october(ACCT_A, "500")], _index(_two_workloads()), FindingsRuleset(),
                       TAG_KEYS, AS_OF)
    assert {s.method for s in result.spend} == {"none"}


def test_tag_is_primary_over_resource_level_and_both_are_kept() -> None:
    index = _index(build_result([workload("w1", [(I_W1, "declared")])]),
                   tags={I_W1.key_str(): {"app": "orders"}})
    facts = [_tag("orders", ACCT_A, *TAG_MONTHS[2]), _window(ACCT_A, "20"),
             _day("i-w1", ACCT_A, "13")]
    result = attribute(facts, index, FindingsRuleset(), TAG_KEYS, AS_OF)
    assert [(s.method, s.primary) for s in result.spend] == [("tag", True),
                                                              ("resource_level", False)]


def test_disagreement_compares_daily_averages_and_fires_only_above_the_ratio() -> None:
    index = _index(build_result([workload("w1", [(I_W1, "declared")])]),
                   tags={I_W1.key_str(): {"app": "orders"}})
    # Tag: 31 over October (31 days) = 1.00/day. Resource level over 13 days.
    agree = [_tag("orders", ACCT_A, "2026-10-01", "2026-11-01", "31"), _window(ACCT_A, "20"),
             _day("i-w1", ACCT_A, "13")]  # 1.00/day
    assert not [f for f in attribute(agree, index, FindingsRuleset(), TAG_KEYS, AS_OF).findings
                if f.rule_id == "attribution_disagreement"]
    disagree = [_tag("orders", ACCT_A, "2026-10-01", "2026-11-01", "31"), _window(ACCT_A, "20"),
                _day("i-w1", ACCT_A, "6.5")]  # 0.50/day: difference 0.5 > 0.25
    found = [f for f in attribute(disagree, index, FindingsRuleset(), TAG_KEYS, AS_OF).findings
             if f.rule_id == "attribution_disagreement"]
    assert len(found) == 1
    assert found[0].values["relative_difference"] == "0.5"
    assert found[0].workload_id == "w1"


def test_a_workload_with_no_method_has_no_amount() -> None:
    result = attribute([], _index(_two_workloads()), FindingsRuleset(), TAG_KEYS, AS_OF)
    assert all(s.method == "none" and s.amount is None and s.primary for s in result.spend)


def test_mixed_currencies_in_an_account_window_raise_currency_mismatch() -> None:
    facts = [_window(ACCT_A, "100"), _day("i-w1", ACCT_A, "40", unit="EUR")]
    result = attribute(facts, _index(_two_workloads()), FindingsRuleset(), TAG_KEYS, AS_OF)
    assert [f.rule_id for f in result.findings] == ["currency_mismatch"]
    assert result.buckets == ()
