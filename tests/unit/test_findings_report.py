"""F15-F17: guardrail, narrative fallback, report assembly, determinism, CLI. Offline."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from wg_cost_builders import AS_OF, scenario
from wg_test_helpers import ACCT_A, ACCT_B

from workload_graph.cli import main
from workload_graph.cost.snapshots import dump_cost_snapshot, resolve_ref
from workload_graph.findings.models import FindingKind
from workload_graph.pipeline_findings import run
from workload_graph.report.guardrail import check_narrative
from workload_graph.report.models import (
    SECTION_NAMES,
    FindingsReport,
    Narrative,
    NarrativeSection,
    Sentence,
)
from workload_graph.report.narrative import BedrockNarrator, TemplateNarrator


def _report(**kw: object) -> FindingsReport:
    built, cost, graph = scenario(ACCT_A, **kw)  # type: ignore[arg-type]
    return run(built, cost, as_of=AS_OF, graph_snapshots=graph)


def _rule(report: FindingsReport, rule: str) -> list:  # type: ignore[type-arg]
    return [f for f in report.findings if f.rule_id == rule]


def _codes(report: FindingsReport, text: str, ids: tuple[str, ...]) -> set[str]:
    narrative = Narrative(sections=(NarrativeSection(
        name="summary", sentences=(Sentence(text=text, finding_ids=ids),)),))
    return {v.code for v in check_narrative(narrative, report).violations}


# --- the standard scenario, end to end --------------------------------------------------------

def test_the_standard_scenario_figures() -> None:
    report = _report()
    change = _rule(report, "spend_change")[0]
    assert (change.values["prior"], change.values["current"], change.values["amount"]) == \
        ("1200", "1850", "650")
    figures = {f.name: f for f in report.summary}
    assert figures["deduplicated_savings_total"].value == "550"
    assert figures["quick_win_sum"].value == "120"
    assert figures["latest_month_total"].finding_ids == (change.finding_id,)
    (orders,) = report.workloads
    assert orders.name_hint == "orders"
    assert orders.savings_sum_of_resource_recommendations == "120"
    primary = [s for s in orders.spend if s.primary][0]
    assert (primary.method, primary.amount) == ("tag", Decimal(400))
    assert [f.display_id for f in report.findings] == \
        [f"F-{i:03d}" for i in range(1, len(report.findings) + 1)]


def test_sections_follow_the_fixed_order() -> None:
    order = [FindingKind.SPEND_CHANGE, FindingKind.SPEND_DRIVER, FindingKind.WORKLOAD_SPEND,
             FindingKind.SAVINGS_OPPORTUNITY, FindingKind.COMMITMENT_OPPORTUNITY,
             FindingKind.DATA_QUALITY]
    kinds = [order.index(f.kind) for f in _report().findings]
    assert kinds == sorted(kinds)


def test_buckets_reconcile_exactly() -> None:
    for table in _report().buckets:
        assert sum(table.rows.values(), Decimal(0)) == table.total
    (table,) = _report().buckets
    assert table.total == Decimal(130) and table.rows["workload:w1"] == Decimal(40)


def test_every_citation_resolves_to_a_real_datum() -> None:
    built, cost, graph = scenario(ACCT_A)
    report = run(built, cost, as_of=AS_OF, graph_snapshots=graph)
    by_id = {s.snapshot_id: s for s in cost}
    ids = {f.finding_id for f in report.findings}
    for finding in report.findings:
        for ev in finding.evidence:
            resolve_ref(by_id, ev.raw_ref)
        assert report.evidence_index[finding.finding_id]
    for section in report.narrative.sections:
        for sentence in section.sentences:
            assert sentence.finding_ids and set(sentence.finding_ids) <= ids
    for figure in report.summary:
        assert set(figure.finding_ids) <= ids


def test_the_report_has_no_floats_and_a_trailing_newline() -> None:
    text = _report().to_json()
    assert text.endswith("}\n")

    def walk(value: object) -> None:
        assert not isinstance(value, float)
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(json.loads(text))


def test_the_template_narrative_passes_the_guardrail_in_every_section() -> None:
    report = _report()
    assert check_narrative(report.narrative, report).ok
    assert [s.name for s in report.narrative.sections] == list(SECTION_NAMES)
    assert not [n for n in report.notes if n.startswith("narrative")]


# --- scenario variants ------------------------------------------------------------------------

def test_without_resource_level_data_there_are_no_buckets_and_an_ask() -> None:
    report = _report(resource_level=False)
    assert report.buckets == ()
    assert _rule(report, "resource_level_not_enabled")
    assert "A-RESOURCE-LEVEL" in {a.ask_id for a in report.customer_asks}
    assert report.coverage.resource_level_visible is False
    assert {f.name: f for f in report.summary}["unattributed_share"].value is None


def test_without_tag_data_the_tag_key_is_reported_inactive_or_unused() -> None:
    report = _report(tagged=False)
    (gap,) = _rule(report, "tag_cost_not_visible")
    assert gap.values["tag_key"] == "app"
    assert "A-TAG-ACTIVATE:app" in {a.ask_id for a in report.customer_asks}
    assert report.coverage.tag_cost_visible is False


def test_hub_not_enabled_gives_the_opt_in_ask_and_no_savings() -> None:
    report = _report(coh=False)
    assert not [f for f in report.findings if f.kind is FindingKind.SAVINGS_OPPORTUNITY]
    assert "A-COH-OPTIN" in {a.ask_id for a in report.customer_asks}
    hub = [e for e in report.coverage.entries if e.source == "cost_optimization_hub"][0]
    assert hub.status == "not_enabled"
    assert {f.name: f for f in report.summary}["deduplicated_savings_total"].value is None


def test_without_graph_snapshots_tag_attribution_is_noted() -> None:
    built, cost, _ = scenario(ACCT_A)
    report = run(built, cost, as_of=AS_OF)
    assert "graph snapshots not given: tag attribution unavailable" in report.notes
    assert not [s for s in report.workloads[0].spend if s.method == "tag"]


def test_unapproved_workloads_are_left_out() -> None:
    built, cost, graph = scenario(ACCT_A)
    report = run(built, cost, as_of=AS_OF, graph_snapshots=graph, approved=set())
    assert report.workloads == ()
    assert report.buckets[0].rows.get("workload:w1") is None


def test_two_accounts_add_an_aggregate_equal_to_the_sum() -> None:
    a_built, a_cost, a_graph = scenario(ACCT_A)
    _, b_cost, _ = scenario(ACCT_B)
    report = run(a_built, a_cost + b_cost, as_of=AS_OF, graph_snapshots=a_graph)
    changes = {f.account_id: f for f in _rule(report, "spend_change")}
    assert set(changes) == {None, ACCT_A, ACCT_B}
    assert changes[None].values["current"] == "3700"
    assert {f.name: f for f in report.summary}["deduplicated_savings_total"].value == "1100"


def test_a_missing_snapshot_is_a_coverage_error() -> None:
    built, cost, graph = scenario(ACCT_A)
    report = run(built, cost[:1], as_of=AS_OF, graph_snapshots=graph)
    hub = [e for e in report.coverage.entries if e.source == "cost_optimization_hub"][0]
    assert hub.status == "error"
    assert f"cost_optimization_hub snapshot missing for {ACCT_A}" in report.notes


# --- guardrail ----------------------------------------------------------------------------------

def test_guardrail_codes() -> None:
    report = _report()
    change = _rule(report, "spend_change")[0].finding_id
    driver = _rule(report, "spend_driver")[0].finding_id
    assert _codes(report, "Spend rose.", ()) == {"G1_NO_CITATION"}
    assert _codes(report, "Spend rose.", ("F-000000000000",)) == {"G2_UNKNOWN_FINDING"}
    assert _codes(report, "Spend rose to USD 1,851.00.", (change,)) == {"G3_NUMBER_MISMATCH"}
    assert _codes(report, "Spend rose to USD 1850.", (change,)) == {"G3_NUMBER_MISMATCH"}
    assert _codes(report, "Spend rose by 55%.", (change,)) == {"G3_NUMBER_MISMATCH"}
    assert _codes(report, "Spend rose by 651.", (change,)) == {"G3_NUMBER_MISMATCH"}
    assert _codes(report, "Spend rose by 650 in 2026-10.", (change,)) == set()
    assert _codes(report, "Spend rose because of EC2.", (change,)) == {"G4_CAUSAL_CLAIM"}
    assert _codes(report, "Spend rose, driven by EC2.", (change,)) == {"G4_CAUSAL_CLAIM"}
    assert _codes(report, "The change was driven by Amazon EC2.", (driver,)) == set()
    assert _codes(report, 'Spend for workload "payments" rose.', (change,)) == \
        {"G5_UNKNOWN_WORKLOAD"}
    assert _codes(report, "x" * 401, (change,)) == {"G6_TOO_LONG"}
    assert _codes(report, f"Account {ACCT_A} spend rose.", (change,)) == set()


class _BadNarrator:
    def narrate(self, core: FindingsReport) -> Narrative:
        good = TemplateNarrator().narrate(core)
        bad = NarrativeSection(name="summary", sentences=(
            Sentence(text="Spend doubled because of a launch.", finding_ids=()),))
        return Narrative(sections=tuple(bad if s.name == "summary" else s
                                        for s in good.sections))


def test_a_failing_section_is_replaced_by_the_template_and_noted() -> None:
    built, cost, graph = scenario(ACCT_A)
    report = run(built, cost, as_of=AS_OF, graph_snapshots=graph, narrator=_BadNarrator())
    template = run(built, cost, as_of=AS_OF, graph_snapshots=graph)
    assert report.narrative == template.narrative
    assert "narrative section summary replaced by template: G1_NO_CITATION,G4_CAUSAL_CLAIM" \
        in report.notes


def test_the_model_narrator_stub_falls_back_to_the_template() -> None:
    with pytest.raises(NotImplementedError, match="SKELETON"):
        BedrockNarrator().narrate(_report())
    built, cost, graph = scenario(ACCT_A)
    report = run(built, cost, as_of=AS_OF, graph_snapshots=graph, narrator=BedrockNarrator())
    assert "narrator failed (NotImplementedError); template narrative used" in report.notes
    assert check_narrative(report.narrative, report).ok


# --- determinism --------------------------------------------------------------------------------

def test_input_order_does_not_change_a_byte() -> None:
    built, cost, graph = scenario(ACCT_A)
    _, b_cost, _ = scenario(ACCT_B)
    everything = cost + b_cost
    expected = run(built, everything, as_of=AS_OF, graph_snapshots=graph).to_json()
    rng = random.Random(7)  # noqa: S311
    for _ in range(5):
        shuffled = everything[:]
        rng.shuffle(shuffled)
        assert run(built, shuffled, as_of=AS_OF, graph_snapshots=graph).to_json() == expected


def _write_inputs(root: Path) -> None:
    built, cost, graph = scenario(ACCT_A)
    (root / "build.json").write_text(built.to_json(), encoding="utf-8")
    for snapshot in cost:
        folder = root / "cost" / snapshot.account_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{snapshot.source}.json").write_text(dump_cost_snapshot(snapshot),
                                                         encoding="utf-8")
    for snapshot in graph:
        folder = root / "graph" / snapshot.account_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{snapshot.source}.json").write_text(snapshot.model_dump_json(),
                                                         encoding="utf-8")


def _args(root: Path, out: str) -> list[str]:
    return ["findings", "--build-result", str(root / "build.json"), "--snapshots",
            str(root / "cost"), "--graph-snapshots", str(root / "graph"),
            "--as-of", AS_OF.isoformat(), "--out", str(root / out)]


def test_cli_writes_the_report_and_matches_the_library(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    assert main(_args(tmp_path, "report.json")) == 0
    built, cost, graph = scenario(ACCT_A)
    expected = run(built, cost, as_of=AS_OF, graph_snapshots=graph)
    written = FindingsReport.model_validate_json((tmp_path / "report.json").read_text())
    assert written.findings == expected.findings


def test_cli_bad_input_exits_2(tmp_path: Path) -> None:
    assert main(["findings", "--build-result", str(tmp_path / "missing.json"), "--snapshots",
                 str(tmp_path), "--as-of", "2026-11-19", "--out",
                 str(tmp_path / "r.json")]) == 2
    assert main(["findings", "--build-result", "x", "--snapshots", "y", "--as-of", "nope",
                 "--out", "z"]) == 2


def test_hash_seed_does_not_change_a_byte(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    outputs = []
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "workload_graph.cli", *_args(tmp_path, f"r{seed}.json")],
            check=True, env=env, capture_output=True)
        outputs.append((tmp_path / f"r{seed}.json").read_bytes())
    assert outputs[0] == outputs[1] == outputs[2]
