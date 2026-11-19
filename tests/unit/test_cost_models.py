"""F2 models and findings ruleset; F3 cost snapshots and the pointer resolver. Offline."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from wg_test_helpers import ACCT_A, ACCT_B

from workload_graph.cost.models import AccessPlan, CostFact, CostScope
from workload_graph.cost.periods import Period
from workload_graph.cost.snapshots import (
    dump_cost_snapshot,
    escape_pointer,
    load_cost_snapshot,
    resolve_pointer,
    resolve_ref,
)
from workload_graph.findings.models import Confidence, Finding, FindingKind, min_confidence
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.models import COST_SOURCES, SOURCES, Evidence, RawSnapshot
from workload_graph.pipeline import load_snapshots

AT = datetime(2026, 11, 19, tzinfo=UTC)


def _ev(ref: str = "snap#/payload/x/0") -> Evidence:
    return Evidence(source="cost_explorer", observed_at=AT, raw_ref=ref, detail="test")


def _finding(**overrides: object) -> Finding:
    args: dict[str, object] = {
        "kind": FindingKind.SPEND_CHANGE, "rule_id": "spend_change", "rule_version": "1",
        "workload_id": None, "account_id": ACCT_A, "confidence": Confidence.HIGH,
        "values": {"amount": "120.50", "unit": "USD"}, "evidence": (_ev(),),
        "subject": {"scope": ACCT_A},
    }
    args.update(overrides)
    return Finding.create(**args)  # type: ignore[arg-type]


# --- F2 models -------------------------------------------------------------------------------

def test_access_plan_validates_ids_and_needs_linked_accounts() -> None:
    plan = AccessPlan(access_account_id=ACCT_A, linked_account_ids=(ACCT_B, ACCT_A, ACCT_B))
    assert plan.linked_account_ids == (ACCT_A, ACCT_B)
    with pytest.raises(ValidationError):
        AccessPlan(access_account_id="123", linked_account_ids=(ACCT_A,))
    with pytest.raises(ValidationError):
        AccessPlan(access_account_id=ACCT_A, linked_account_ids=())


def test_cost_fact_round_trips_with_decimal_as_string() -> None:
    fact = CostFact(
        fact_id="f1", kind="account_service_month",
        dimensions={"service": "Amazon EC2", "linked_account": ACCT_A},
        period=Period(start=date(2026, 10, 1), end=date(2026, 11, 1)),
        amount=Decimal("12.30"), unit="USD", estimated=False, evidence=_ev())
    text = fact.model_dump_json()
    assert '"amount":"12.30"' in text
    again = CostFact.model_validate_json(text)
    assert again == fact
    assert again.amount == Decimal("12.30")
    assert list(again.dimensions) == ["linked_account", "service"]
    with pytest.raises(ValidationError):
        fact.amount = Decimal(1)  # type: ignore[misc]


def test_cost_scope_round_trips() -> None:
    scope = CostScope(as_of=date(2026, 11, 19), lookback_months=3, metric="UnblendedCost",
                      max_calls=40, resource_level_window_days=13,
                      resource_level_top_services=5, tag_keys=("app",))
    assert CostScope.model_validate_json(scope.model_dump_json()) == scope


def test_min_confidence_truth_table() -> None:
    levels = [Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW]
    for a in levels:
        for b in levels:
            assert min_confidence(a, b) == (a if a.rank <= b.rank else b)
    assert [c.rank for c in levels] == [3, 2, 1]


def test_finding_id_is_stable_and_changes_with_the_subject() -> None:
    first, second = _finding(), _finding()
    assert first.finding_id == second.finding_id
    assert first.finding_id.startswith("F-") and len(first.finding_id) == 14
    assert _finding(subject={"scope": ACCT_B}).finding_id != first.finding_id
    assert _finding(rule_version="2").finding_id != first.finding_id


def test_finding_requires_evidence_and_round_trips() -> None:
    with pytest.raises(ValidationError):
        _finding(evidence=())
    finding = _finding(flags=("b", "a"))
    assert finding.flags == ("a", "b")
    assert Finding.model_validate_json(finding.model_dump_json()) == finding
    assert finding.amount() == Decimal("120.50")
    assert finding.pillar == "cost_optimization"


def test_rendered_values_cover_money_pct_and_integers() -> None:
    finding = _finding(values={"amount": "6900", "pct": "0.092", "count": 12, "unit": "USD"})
    rendered = finding.rendered_values()
    assert {"USD 6,900.00", "9.2%", "12"} <= rendered
    assert "USD 6900" not in rendered


# --- F2 ruleset ------------------------------------------------------------------------------

def test_findings_ruleset_defaults_validate_and_hash_stably() -> None:
    ruleset = FindingsRuleset()
    assert ruleset.resource_level_window_days == 13
    assert ruleset.driver_min_abs == Decimal("100.00")
    assert ruleset.content_hash() == FindingsRuleset().content_hash()
    reordered = json.loads(ruleset.canonical_json())
    reversed_json = json.dumps(dict(reversed(list(reordered.items()))))
    assert FindingsRuleset.model_validate_json(reversed_json).content_hash() == \
        ruleset.content_hash()


def test_findings_ruleset_hash_changes_with_any_field() -> None:
    base = FindingsRuleset().content_hash()
    assert FindingsRuleset(driver_top_n=4).content_hash() != base
    assert FindingsRuleset(tag_min_member_share=Decimal("0.7")).content_hash() != base


@pytest.mark.parametrize("bad", [
    {"unknown_key": 1}, {"tag_min_member_share": "1.5"}, {"unplaced_share_flag": "-0.1"},
    {"quick_win_efforts": []}, {"method_confidence": {"tag": "certain"}},
])
def test_findings_ruleset_rejects_bad_values(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FindingsRuleset.model_validate(bad)


def test_findings_ruleset_save_and_load(tmp_path: Path) -> None:
    path = tmp_path / "findings.json"
    FindingsRuleset().save(path)
    assert FindingsRuleset.load(path) == FindingsRuleset()


# --- F3 snapshots ----------------------------------------------------------------------------

def _cost_file(tmp_path: Path) -> Path:
    path = tmp_path / ACCT_A / "cost_optimization_hub.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"snapshot_id": "coh-1", "source": "cost_optimization_hub", '
        f'"account_id": "{ACCT_A}", "region": "us-east-1", '
        '"collected_at": "2026-11-19T00:00:00Z", "operations_called": [], "status": "ok", '
        '"notes": ["first"], "payload": {"ListRecommendations:list_recommendations": '
        '[{"items": [{"estimatedMonthlySavings": 12.30, "a/b": {"c~d": 1}}]}]}}')
    return path


def test_cost_sources_are_known_but_not_graph_sources() -> None:
    assert set(COST_SOURCES) == {"cost_explorer", "cost_optimization_hub"}
    assert not set(COST_SOURCES) & set(SOURCES)


def test_cost_snapshots_load_numbers_as_decimal(tmp_path: Path) -> None:
    snapshot = load_cost_snapshot(_cost_file(tmp_path))
    item = snapshot.payload["ListRecommendations:list_recommendations"][0]["items"][0]
    assert item["estimatedMonthlySavings"] == Decimal("12.30")
    assert isinstance(item["estimatedMonthlySavings"], Decimal)


def test_cost_snapshot_dump_round_trips_amounts_exactly(tmp_path: Path) -> None:
    snapshot = load_cost_snapshot(_cost_file(tmp_path))
    copy = tmp_path / "copy.json"
    copy.write_text(dump_cost_snapshot(snapshot))
    again = load_cost_snapshot(copy)
    item = again.payload["ListRecommendations:list_recommendations"][0]["items"][0]
    assert Decimal(str(item["estimatedMonthlySavings"])) == Decimal("12.30")
    assert dump_cost_snapshot(again) == dump_cost_snapshot(snapshot)


def test_graph_loader_skips_cost_files_without_a_note(tmp_path: Path) -> None:
    _cost_file(tmp_path)
    snapshots, notes = load_snapshots(tmp_path)
    assert snapshots == []
    assert notes == []


def test_resolve_pointer_finds_the_datum_and_refuses_bad_pointers(tmp_path: Path) -> None:
    snapshot = load_cost_snapshot(_cost_file(tmp_path))
    op = escape_pointer("ListRecommendations:list_recommendations")
    item = f"/payload/{op}/0/items/0"
    assert resolve_pointer(snapshot, f"{item}/estimatedMonthlySavings") == Decimal("12.30")
    assert resolve_pointer(snapshot, f"{item}/{escape_pointer('a/b')}/{escape_pointer('c~d')}") == 1
    assert resolve_pointer(snapshot, "/status") == "ok"
    assert resolve_pointer(snapshot, "/notes/0") == "first"
    for bad in ("payload", "/payload/missing/0", f"{item}/9", "/notes/x"):
        with pytest.raises(KeyError):
            resolve_pointer(snapshot, bad)


def test_resolve_ref_looks_up_the_snapshot_by_id(tmp_path: Path) -> None:
    snapshot = load_cost_snapshot(_cost_file(tmp_path))
    assert resolve_ref({"coh-1": snapshot}, "coh-1#/status") == "ok"
    with pytest.raises(KeyError):
        resolve_ref({"coh-1": snapshot}, "other#/status")


def test_existing_sources_still_load_as_before(tmp_path: Path) -> None:
    folder = tmp_path / ACCT_A
    folder.mkdir()
    raw = RawSnapshot(snapshot_id="t", source="tags", account_id=ACCT_A, region="eu-west-1",
                      collected_at=AT, operations_called=[], payload={}, status="empty")
    (folder / "tags.json").write_text(raw.model_dump_json())
    snapshots, notes = load_snapshots(tmp_path)
    assert snapshots == [raw] and notes == []
