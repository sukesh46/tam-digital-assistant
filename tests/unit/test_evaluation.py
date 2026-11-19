"""Step 12: pairwise metrics and the golden loader. Numbers below are hand-computed."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from workload_graph.evaluation.golden import load_golden
from workload_graph.evaluation.metrics import evaluate
from workload_graph.ids import ResourceKey
from workload_graph.models import (
    BuildResult,
    CandidateWorkload,
    EdgeType,
    Evidence,
    Member,
    ProbeResult,
    SharedInfra,
    Unplaced,
)

ACCT = "111111111111"
EVIDENCE = Evidence(source="stacks", observed_at=datetime(2026, 1, 15, tzinfo=UTC),
                    raw_ref="snap#/payload/0", detail="stack member")


def _key(name: str) -> ResourceKey:
    return ResourceKey(account_id=ACCT, region="eu-west-1",
                       arn=f"arn:aws:ec2:eu-west-1:{ACCT}:instance/{name}")


def _ks(name: str) -> str:
    return _key(name).key_str()


def _member(name: str) -> Member:
    return Member(key=_key(name), membership="declared", affinity=None,
                  reasons=[EdgeType.STACK_MEMBER], evidence=[EVIDENCE])


def _workload(workload_id: str, names: list[str]) -> CandidateWorkload:
    return CandidateWorkload(
        workload_id=workload_id, name_hint=workload_id,
        members=[_member(n) for n in names], confidence="high", accounts=[ACCT],
        sources_used=["stacks"], shared_dependencies=[], external_refs=[],
        evidence_summary={"STACK_MEMBER": len(names)},
    )


def _result(workloads: list[tuple[str, list[str]]], unmapped: list[str] | None = None,
            shared: list[str] | None = None) -> BuildResult:
    return BuildResult(
        ruleset_version="0.1.0", ruleset_hash="a" * 64,
        workloads=[_workload(wid, names) for wid, names in workloads],
        shared_infrastructure=[SharedInfra(key=_key(n), touches=["w"]) for n in shared or []],
        residual_groups=[],
        unmapped=[Unplaced(key=_key(n), reason="no_edges") for n in unmapped or []],
        external_refs=[], probe=ProbeResult(), notes=[], generated_from=["snap"],
    )


def _golden(path: Path, workloads: dict[str, list[str]], shared: list[str] | None = None) -> Path:
    import json

    payload = {
        "scenario": "example",
        "description": "hand-computed",
        "workloads": [{"name": name, "members": [_ks(n) for n in members]}
                      for name, members in workloads.items()],
        "shared": [_ks(n) for n in shared or []],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_pairwise_metrics_match_the_hand_computed_five_resource_example(tmp_path: Path) -> None:
    # Golden pairs: ab, ac, bc, de (4). Predicted pairs: ab, cd (2). Matched: ab (1).
    predicted = _result([("one", ["a", "b"]), ("two", ["c", "d"])], unmapped=["e"])
    golden = load_golden(_golden(tmp_path / "g.json",
                                 {"one": ["a", "b", "c"], "two": ["d", "e"]}))
    report = evaluate(predicted, golden)
    assert report.predicted_pair_count == 2
    assert report.golden_pair_count == 4
    assert report.matched_pair_count == 1
    assert report.pairwise_precision == pytest.approx(0.5)
    assert report.pairwise_recall == pytest.approx(0.25)
    assert report.pairwise_f1 == pytest.approx(1 / 3)
    assert report.coverage == pytest.approx(0.8)
    assert report.unmapped_rate == pytest.approx(0.2)


def test_perfect_prediction_scores_one_on_every_metric(tmp_path: Path) -> None:
    predicted = _result([("one", ["a", "b", "c"]), ("two", ["d", "e"])])
    golden = load_golden(_golden(tmp_path / "g.json",
                                 {"one": ["a", "b", "c"], "two": ["d", "e"]}))
    report = evaluate(predicted, golden)
    assert report.pairwise_precision == pytest.approx(1.0)
    assert report.pairwise_recall == pytest.approx(1.0)
    assert report.pairwise_f1 == pytest.approx(1.0)
    assert report.coverage == pytest.approx(1.0)
    assert report.unmapped_rate == pytest.approx(0.0)


def test_empty_prediction_has_recall_zero(tmp_path: Path) -> None:
    predicted = _result([], unmapped=["a", "b", "c"])
    golden = load_golden(_golden(tmp_path / "g.json", {"one": ["a", "b", "c"]}))
    report = evaluate(predicted, golden)
    assert report.pairwise_recall == pytest.approx(0.0)
    assert report.pairwise_precision == pytest.approx(1.0)
    assert report.pairwise_f1 == pytest.approx(0.0)
    assert report.coverage == pytest.approx(0.0)
    assert report.unmapped_rate == pytest.approx(1.0)


def test_shared_infrastructure_is_excluded_from_pairs_and_reported_separately(
        tmp_path: Path) -> None:
    predicted = _result([("one", ["a", "b"])], shared=["f"])
    golden = load_golden(_golden(tmp_path / "g.json", {"one": ["a", "b"]}, shared=["f"]))
    report = evaluate(predicted, golden)
    assert report.predicted_pair_count == 1
    assert report.golden_pair_count == 1
    assert report.pairwise_f1 == pytest.approx(1.0)
    assert report.shared_infrastructure == [_ks("f")]
    assert report.coverage == pytest.approx(1.0)
    text = report.summary()
    assert "pairwise precision: 1.0000" in text
    assert "shared infrastructure: 1" in text


def test_golden_loader_rejects_unknown_resources(tmp_path: Path) -> None:
    path = _golden(tmp_path / "g.json", {"one": ["a", "missing"]})
    with pytest.raises(ValueError, match="unknown resources"):
        load_golden(path, known_keys={_ks("a")})


def test_golden_loader_accepts_known_resources(tmp_path: Path) -> None:
    path = _golden(tmp_path / "g.json", {"one": ["a"]}, shared=["f"])
    golden = load_golden(path, known_keys={_ks("a"), _ks("f")})
    assert golden.scenario == "example"
    assert golden.workloads[0].members == [_ks("a")]
