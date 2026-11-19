"""F11 drivers, F12 savings, F13 data quality, F14 asks. Hand-computed; offline."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from wg_cost_builders import (
    AS_OF,
    COLLECTED,
    build_result,
    cost_snapshot,
    fact,
    month,
    rkey,
    workload,
)
from wg_test_helpers import ACCT_A, ACCT_B

from workload_graph.cost.labels import LABELS_KEY
from workload_graph.findings.asks import build_asks, build_talking_points
from workload_graph.findings.attribution import MonthAmount, WorkloadSpend
from workload_graph.findings.data_quality import (
    source_findings,
    stale_findings,
    tag_visibility_findings,
)
from workload_graph.findings.drivers import spend_findings
from workload_graph.findings.index import ResourceIndex
from workload_graph.findings.models import Confidence, FindingKind
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.findings.savings import savings_findings
from workload_graph.models import Evidence
from workload_graph.normalisers.cost_optimization_hub import DedupedSummary, Recommendation

SEP, OCT, NOV = "2026-09-01", "2026-10-01", "2026-11-01"


def _of(findings: list, rule: str) -> list:  # type: ignore[type-arg]
    return [f for f in findings if f.rule_id == rule]


# --- F11 drivers ------------------------------------------------------------------------------

def _two_months() -> list[object]:
    return [month("Amazon EC2", ACCT_A, SEP, "1000"), month("Amazon EC2", ACCT_A, OCT, "1500"),
            month("Amazon S3", ACCT_A, SEP, "200"), month("Amazon S3", ACCT_A, OCT, "50"),
            month("Amazon RDS", ACCT_A, OCT, "300"), month("AWS Lambda", ACCT_A, SEP, "120"),
            month("Amazon SQS", ACCT_A, SEP, "10"), month("Amazon SQS", ACCT_A, OCT, "60")]


def test_spend_change_with_exact_delta_and_percentage() -> None:
    findings = spend_findings(_two_months(), [ACCT_A], AS_OF, FindingsRuleset())
    change = _of(findings, "spend_change")[0]
    assert change.values["prior"] == "1330" and change.values["current"] == "1910"
    assert change.values["amount"] == "580"
    assert Decimal(str(change.values["pct"])) == Decimal(580) / Decimal(1330)
    assert change.confidence is Confidence.HIGH


def test_drivers_flags_limits_and_tie_breaks() -> None:
    drivers = _of(spend_findings(_two_months(), [ACCT_A], AS_OF, FindingsRuleset()),
                  "spend_driver")
    # Moves: EC2 +500, RDS +300 (new), S3 -150, Lambda -120 (ended), SQS +50 (< 100).
    assert [d.values["service"] for d in drivers] == ["Amazon EC2", "Amazon RDS", "Amazon S3"]
    rds = drivers[1]
    assert rds.flags == ("new_service",) and rds.values["pct"] is None
    assert rds.values["contribution_share"] == str(Decimal(300) / Decimal(580))
    wider = _of(spend_findings(_two_months(), [ACCT_A], AS_OF, FindingsRuleset(driver_top_n=5)),
                "spend_driver")
    lam = [d for d in wider if d.values["service"] == "AWS Lambda"][0]
    assert lam.flags == ("ended_service",)
    assert len(wider) == 4  # SQS stays under driver_min_abs


def test_ties_are_broken_by_service_name() -> None:
    facts = [month("B svc", ACCT_A, SEP, "0"), month("B svc", ACCT_A, OCT, "200"),
             month("A svc", ACCT_A, SEP, "100"), month("A svc", ACCT_A, OCT, "300")]
    drivers = _of(spend_findings(facts, [ACCT_A], AS_OF, FindingsRuleset()), "spend_driver")
    assert [d.values["service"] for d in drivers] == ["A svc", "B svc"]


def test_zero_total_delta_gives_no_contribution_share() -> None:
    facts = [month("A", ACCT_A, SEP, "100"), month("A", ACCT_A, OCT, "300"),
             month("B", ACCT_A, SEP, "300"), month("B", ACCT_A, OCT, "100")]
    drivers = _of(spend_findings(facts, [ACCT_A], AS_OF, FindingsRuleset()), "spend_driver")
    assert all(d.values["contribution_share"] is None for d in drivers)


def test_estimated_months_are_excluded_and_missing_history_is_reported() -> None:
    facts = [month("A", ACCT_A, SEP, "100"), month("A", ACCT_A, OCT, "300", estimated=True)]
    findings = spend_findings(facts, [ACCT_A], AS_OF, FindingsRuleset())
    assert not _of(findings, "spend_change") and not _of(findings, "spend_driver")
    assert _of(findings, "insufficient_history")[0].values["missing"] == "current"


def test_zero_prior_total_is_insufficient_history() -> None:
    facts = [month("A", ACCT_A, SEP, "0"), month("A", ACCT_A, OCT, "300")]
    findings = spend_findings(facts, [ACCT_A], AS_OF, FindingsRuleset())
    assert _of(findings, "insufficient_history")
    assert not _of(findings, "spend_change")


def test_currency_mismatch_becomes_a_finding() -> None:
    facts = [month("A", ACCT_A, SEP, "100"), month("B", ACCT_A, SEP, "1", unit="EUR"),
             month("A", ACCT_A, OCT, "300")]
    findings = spend_findings(facts, [ACCT_A], AS_OF, FindingsRuleset())
    assert _of(findings, "currency_mismatch") and not _of(findings, "spend_change")


def test_the_aggregate_scope_equals_the_sum_of_accounts() -> None:
    facts = _two_months() + [month("Amazon EC2", ACCT_B, SEP, "100"),
                             month("Amazon EC2", ACCT_B, OCT, "400")]
    changes = _of(spend_findings(facts, [ACCT_A, ACCT_B], AS_OF, FindingsRuleset()),
                  "spend_change")
    by_account = {c.account_id: c for c in changes}
    assert by_account[None].values["current"] == "2310"
    assert Decimal(str(by_account[None].values["amount"])) == sum(
        Decimal(str(by_account[a].values["amount"])) for a in (ACCT_A, ACCT_B))
    assert len(_of(spend_findings(facts, [ACCT_A], AS_OF, FindingsRuleset()),
                   "spend_change")) == 1


def test_the_partial_month_is_reported_alone_and_never_compared() -> None:
    facts = _two_months() + [fact("account_service_month",
                                  {"service": "Amazon EC2", "linked_account": ACCT_A},
                                  NOV, "2026-11-19", "900", estimated=True)]
    findings = spend_findings(facts, [ACCT_A], AS_OF, FindingsRuleset())
    partial = _of(findings, "spend_change_partial_month")[0]
    assert partial.values["amount"] == "900" and partial.values["estimated"] is True
    assert partial.flags == ("estimated",) and partial.confidence is Confidence.MEDIUM
    assert _of(findings, "spend_change")[0].values["current"] == "1910"


def test_workload_trend_comes_from_a_tag_series() -> None:
    from workload_graph.cost.periods import complete_months

    months = complete_months(AS_OF, 3)
    ev = Evidence(source="cost_explorer", observed_at=COLLECTED, raw_ref="ce#/x", detail="t")
    entry = WorkloadSpend(
        workload_id="w1", method="tag", primary=True, amount=Decimal(30), unit="USD",
        period=months[-1], confidence=Confidence.MEDIUM, evidence=(ev,),
        series=tuple(MonthAmount(period=m, amount=Decimal(a))
                     for m, a in zip(months, ("10", "20", "30"), strict=True)))
    trend = _of(spend_findings([], [ACCT_A], AS_OF, FindingsRuleset(), [entry]),
                "workload_spend")[0]
    assert trend.workload_id == "w1" and trend.values["amount"] == "10"
    assert trend.values["month_2026-08"] == "10" and trend.values["pct"] == "0.5"


# --- F12 savings ------------------------------------------------------------------------------

I_DECL = rkey("i-decl", ACCT_A)
I_ATT = rkey("i-att", ACCT_A)
LB = rkey("lb", ACCT_A, kind="loadbalancer")
I_RES = rkey("i-res", ACCT_A)
I_DUP1 = rkey("dup", ACCT_A, kind="volume")
I_DUP2 = rkey("dup", ACCT_A, kind="snapshot")


def _index() -> ResourceIndex:
    return ResourceIndex(build_result(
        [workload("w1", [(I_DECL, "declared"), (I_ATT, "attached"), (I_DUP1, "declared"),
                         (I_DUP2, "declared")], confidence="high")],
        shared=[LB], residual=[I_RES]))


def _rec(rec_id: str, resource: str | None, *, action: str = "Rightsize",
         savings: str = "50", effort: str | None = "Low", restart: bool | None = False,
         rollback: bool | None = True, refreshed: datetime | None = None,
         currency: str = "USD", account: str = ACCT_A) -> Recommendation:
    return Recommendation(
        recommendation_id=rec_id, account_id=account, region="eu-west-1",
        resource_arn=None, resource_id=resource, current_resource_type="Ec2Instance",
        recommended_resource_type=None, action_type=action,
        estimated_monthly_savings=Decimal(savings), estimated_monthly_cost=None,
        estimated_savings_percentage=None, currency=currency, implementation_effort=effort,
        restart_needed=restart, rollback_possible=rollback,
        last_refresh=refreshed or datetime(2026, 11, 18, tzinfo=UTC),
        evidence=Evidence(source="cost_optimization_hub", observed_at=COLLECTED,
                          raw_ref=f"coh#/{rec_id}", detail="t"))


def _summary(account: str, total: str) -> DedupedSummary:
    return DedupedSummary(account_id=account, deduped_total=Decimal(total), currency="USD",
                          evidence=Evidence(source="cost_optimization_hub", observed_at=COLLECTED,
                                            raw_ref=f"coh#/s/{account}", detail="t"))


def _savings(recs: list[Recommendation], summaries: list[DedupedSummary] | None = None,
             primary: dict[str, WorkloadSpend] | None = None) -> object:
    return savings_findings(recs, summaries if summaries is not None else [_summary(ACCT_A,
                            "100000")], _index(), FindingsRuleset(), AS_OF, "USD", primary or {})


def test_commitments_are_account_level_and_absent_from_workload_sums() -> None:
    result = _savings([_rec("c1", "i-decl", action="PurchaseSavingsPlans", savings="400"),
                       _rec("r1", "i-decl", savings="50")])
    commitment = [f for f in result.findings  # type: ignore[attr-defined]
                  if f.kind is FindingKind.COMMITMENT_OPPORTUNITY][0]
    assert commitment.workload_id is None
    assert result.workload_totals == {"w1": Decimal(50)}  # type: ignore[attr-defined]


def test_tiering_needs_low_effort_and_no_restart() -> None:
    result = _savings([_rec("a", "i-decl", effort="Low", restart=False),
                       _rec("b", "i-decl", effort="Low", restart=None),
                       _rec("c", "i-decl", effort="High", restart=False),
                       _rec("d", "i-decl", effort="VeryLow", restart=True)])
    tiers = {f.values["recommendation_id"]: f.tier for f in result.findings}  # type: ignore[attr-defined]
    assert tiers == {"a": "quick_win", "b": "plan_first", "c": "plan_first", "d": "plan_first"}


def test_no_rollback_and_stale_flags() -> None:
    result = _savings([_rec("a", "i-decl", rollback=False,
                            refreshed=datetime(2026, 10, 1, tzinfo=UTC))])
    finding = result.findings[0]  # type: ignore[attr-defined]
    assert finding.flags == ("no_rollback", "stale")
    assert result.stale == {ACCT_A: (finding.evidence[0],)}  # type: ignore[attr-defined]


def test_every_placement_outcome_and_confidence() -> None:
    result = _savings([_rec("decl", "i-decl"), _rec("att", "i-att"), _rec("lb", LB.arn),
                       _rec("res", "i-res"), _rec("unk", "i-nothere"), _rec("dup", "dup"),
                       _rec("none", None)])
    by_id = {f.values["recommendation_id"]: f for f in result.findings}  # type: ignore[attr-defined]
    assert by_id["decl"].workload_id == "w1" and by_id["decl"].confidence is Confidence.HIGH
    assert by_id["att"].workload_id == "w1" and by_id["att"].confidence is Confidence.MEDIUM
    assert by_id["lb"].flags == ("shared_infrastructure",) and by_id["lb"].workload_id is None
    assert by_id["res"].flags == ("resource_not_in_workload",)
    assert by_id["unk"].flags == ("resource_not_in_graph",)
    assert by_id["dup"].flags == ("resource_id_ambiguous",)
    assert by_id["none"].flags == ("resource_not_in_graph",)
    assert by_id["lb"].confidence is Confidence.HIGH


def test_overlap_fires_only_above_the_tolerance_and_carries_both_numbers() -> None:
    recs = [_rec("a", "i-decl", savings="60"), _rec("b", "i-att", savings="50")]
    over = _savings(recs, [_summary(ACCT_A, "100")])
    overlap = [f for f in over.findings if f.rule_id == "savings_overlap"][0]  # type: ignore[attr-defined]
    assert (overlap.values["sum_of_items"], overlap.values["deduplicated_total"]) == ("110", "100")
    assert overlap.values["rule"] == "do not add these figures"
    within = _savings(recs, [_summary(ACCT_A, "109.99")])
    assert not [f for f in within.findings if f.rule_id == "savings_overlap"]  # type: ignore[attr-defined]


def test_missing_summary_is_a_note_and_no_authoritative_total() -> None:
    result = _savings([_rec("a", "i-decl")], [])
    assert result.account_deduped == {}  # type: ignore[attr-defined]
    assert any("no deduplicated savings total" in n for n in result.notes)  # type: ignore[attr-defined]


def test_percent_of_workload_spend_needs_a_monthly_primary_with_the_same_unit() -> None:
    from workload_graph.cost.periods import complete_months

    month_period = complete_months(AS_OF, 1)[0]
    monthly = WorkloadSpend(workload_id="w1", method="tag", primary=True, amount=Decimal(200),
                            unit="USD", period=month_period, confidence=Confidence.MEDIUM)
    window = monthly.model_copy(update={"method": "resource_level"})
    with_pct = _savings([_rec("a", "i-decl", savings="50")], primary={"w1": monthly})
    assert with_pct.findings[0].values["pct_of_workload_spend"] == "0.25"  # type: ignore[attr-defined]
    without = _savings([_rec("a", "i-decl", savings="50")], primary={"w1": window})
    assert "pct_of_workload_spend" not in without.findings[0].values  # type: ignore[attr-defined]


def test_a_recommendation_in_another_currency_is_flagged() -> None:
    result = _savings([_rec("a", "i-decl", currency="EUR")])
    rules = sorted(f.rule_id for f in result.findings)  # type: ignore[attr-defined]
    assert rules == ["currency_mismatch", "savings_opportunity"]


# --- F13 data quality -------------------------------------------------------------------------

def test_source_unavailable_and_resource_level_findings_point_at_the_call_record() -> None:
    snapshot = cost_snapshot("cost_explorer", ACCT_A, {}, statuses={
        "spend_by_service_account_monthly": ("GetCostAndUsage", "denied", "AccessDenied"),
        "window_total_by_account": ("GetCostAndUsage", "denied", "AccessDenied"),
        "resource_costs_daily:Amazon EC2": ("GetCostAndUsageWithResources", "not_enabled",
                                            "DataUnavailableException")})
    findings = source_findings([snapshot])
    denied = [f for f in findings if f.values.get("status") == "denied"][0]
    assert denied.values["label_count"] == 2 and denied.values["codes"] == "AccessDenied"
    assert all(e.raw_ref.startswith(f"{snapshot.snapshot_id}#/payload/{LABELS_KEY}/")
               for e in denied.evidence)
    level = [f for f in findings if f.rule_id == "resource_level_not_enabled"][0]
    assert level.values["services"] == "Amazon EC2"


def test_tag_cost_not_visible_says_inactive_or_unused() -> None:
    ev = Evidence(source="cost_explorer", observed_at=COLLECTED, raw_ref="ce#/tags", detail="t")
    findings = tag_visibility_findings({"app": ["w1"], "team": ["w2"]}, ["TEAM"], {ACCT_A: ev})
    assert [f.values["tag_key"] for f in findings] == ["app"]
    assert "inactive or unused" in str(findings[0].values["explanation"])


def test_stale_findings_count_per_account() -> None:
    ev = Evidence(source="cost_optimization_hub", observed_at=COLLECTED, raw_ref="c#/1",
                  detail="t")
    ev2 = ev.model_copy(update={"raw_ref": "c#/2"})
    findings = stale_findings({ACCT_A: [ev, ev2], ACCT_B: []}, 14)
    assert [(f.account_id, f.values["count"]) for f in findings] == [(ACCT_A, 2)]


# --- F14 asks ---------------------------------------------------------------------------------

def test_each_trigger_yields_its_ask_with_the_triggering_ids() -> None:
    snapshot = cost_snapshot("cost_optimization_hub", ACCT_A, {}, statuses={
        "list_recommendations": ("ListRecommendations", "not_enabled", "AccessDeniedException")})
    denied = cost_snapshot("cost_explorer", ACCT_A, {}, statuses={
        "spend_by_service_account_monthly": ("GetCostAndUsage", "denied", "AccessDenied"),
        "resource_costs_daily:Amazon EC2": ("GetCostAndUsageWithResources", "not_enabled",
                                            "DataUnavailableException")})
    ev = Evidence(source="cost_explorer", observed_at=COLLECTED, raw_ref="ce#/tags", detail="t")
    findings = source_findings([snapshot, denied]) + tag_visibility_findings(
        {"app": ["w1"]}, [], {ACCT_A: ev})
    low = {"w1": workload("w1", [(I_DECL, "declared")], confidence="low", name="orders")}
    asks = build_asks(findings, low)
    by_id = {a.ask_id: a for a in asks}
    assert set(by_id) == {"A-RESOURCE-LEVEL", "A-TAG-ACTIVATE:app", "A-COH-OPTIN",
                          "A-ACCESS:cost_explorer", "A-CONFIRM-WORKLOADS"}
    assert "Amazon EC2" in by_id["A-RESOURCE-LEVEL"].text
    assert "'app'" in by_id["A-TAG-ACTIVATE:app"].text
    assert "Cost Explorer" in by_id["A-ACCESS:cost_explorer"].text
    assert "orders" in by_id["A-CONFIRM-WORKLOADS"].text
    optin = [f.finding_id for f in findings if f.values.get("status") == "not_enabled"
             and f.values.get("source") == "cost_optimization_hub"]
    assert by_id["A-COH-OPTIN"].finding_ids == tuple(optin)
    assert build_asks(findings, low) == asks


def test_talking_points_exist_only_with_their_source_finding() -> None:
    assert build_talking_points([], []) == []
    drivers = _of(spend_findings(_two_months(), [ACCT_A], AS_OF, FindingsRuleset()),
                  "spend_driver")
    quick = _savings([_rec("a", "i-decl", savings="1234.5")]).findings  # type: ignore[attr-defined]
    points = build_talking_points(list(drivers) + list(quick), [])
    assert [p.point_id for p in points] == ["T-DRIVER", "T-QUICKWIN"]
    assert "USD 500.00" in points[0].text and "USD 1,234.50" in points[1].text
