"""Step 13: offline pipeline and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from wg_test_helpers import RAW_DIR, make_snapshot

from workload_graph.cli import main
from workload_graph.models import BuildResult, Workspace
from workload_graph.pipeline import run
from workload_graph.ruleset import Ruleset

S1 = RAW_DIR / "s1"


def _ruleset(tmp_path: Path) -> Path:
    path = tmp_path / "ruleset.json"
    Ruleset().save(path)
    return path


def test_pipeline_builds_a_valid_result_over_a_scenario_directory() -> None:
    result = run(S1, Ruleset(), Workspace.model_validate_json((S1 / "workspace.json").read_text()))
    assert result.workloads
    assert result.ruleset_hash == Ruleset().content_hash()
    again = run(S1, Ruleset(),
                Workspace.model_validate_json((S1 / "workspace.json").read_text()))
    assert again.to_json() == result.to_json()


def test_unreadable_snapshot_is_noted_and_does_not_crash(tmp_path: Path) -> None:
    account = tmp_path / "snaps" / "111111111111"
    account.mkdir(parents=True)
    tags = make_snapshot("tags", {"GetResources": [{"ResourceTagMappingList": []}]})
    (account / "tags.json").write_text(tags.model_dump_json(), encoding="utf-8")
    (account / "stacks.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "snaps" / "notes").mkdir()
    workspace = Workspace(workspace_id="ws", customer="example-customer",
                          approved_account_ids=["111111111111"], regions=["eu-west-1"])
    result = run(tmp_path / "snaps", Ruleset(), workspace)
    assert any("unreadable snapshot 111111111111/stacks.json" in n for n in result.notes)
    assert any("ignored path notes" in n for n in result.notes)
    BuildResult.model_validate_json(result.to_json())


def test_cli_build_writes_a_result_and_repeats_identically(tmp_path: Path) -> None:
    out = tmp_path / "out" / "result.json"
    args = ["build", "--snapshots", str(S1), "--workspace", str(S1 / "workspace.json"),
            "--ruleset", str(_ruleset(tmp_path)), "--out", str(out)]
    assert main(args) == 0
    first = out.read_text(encoding="utf-8")
    BuildResult.model_validate_json(first)
    assert main(args) == 0
    assert out.read_text(encoding="utf-8") == first


def test_cli_evaluate_prints_a_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result_path = tmp_path / "result.json"
    assert main(["build", "--snapshots", str(S1), "--workspace", str(S1 / "workspace.json"),
                 "--ruleset", str(_ruleset(tmp_path)), "--out", str(result_path)]) == 0
    code = main(["evaluate", "--result", str(result_path),
                 "--golden", str(RAW_DIR.parent / "golden" / "s1.json")])
    assert code == 0
    captured = capsys.readouterr().out
    assert "pairwise precision:" in captured
    assert "coverage:" in captured


def test_cli_bad_input_exits_2(tmp_path: Path) -> None:
    assert main(["build"]) == 2
    missing = tmp_path / "missing"
    assert main(["build", "--snapshots", str(missing), "--workspace", str(missing),
                 "--ruleset", str(missing), "--out", str(tmp_path / "out.json")]) == 2
    bad_golden = tmp_path / "golden.json"
    bad_golden.write_text(json.dumps({
        "scenario": "x", "workloads": [{"name": "n", "members": ["not-a-real-key"]}],
        "shared": [],
    }), encoding="utf-8")
    result_path = tmp_path / "result.json"
    assert main(["build", "--snapshots", str(S1), "--workspace", str(S1 / "workspace.json"),
                 "--ruleset", str(_ruleset(tmp_path)), "--out", str(result_path)]) == 0
    assert main(["evaluate", "--result", str(result_path), "--golden", str(bad_golden)]) == 2


def test_cli_collect_refuses_to_run() -> None:
    with pytest.raises(NotImplementedError, match="SKELETON: live wiring requires approved roles"):
        main(["collect"])
