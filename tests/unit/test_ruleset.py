"""Step 3: ruleset tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from workload_graph.models import EdgeType
from workload_graph.ruleset import Ruleset


def test_defaults_validate() -> None:
    rs = Ruleset()
    assert rs.version
    assert rs.edge_weights[EdgeType.LB_TARGET] == 0.8
    assert rs.attach_threshold == 0.6
    assert "app" in rs.workload_tag_keys


def test_unknown_edge_type_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Ruleset.model_validate({"edge_weights": {"NOT_AN_EDGE": 0.5}})


def test_grouping_edge_types_cannot_be_weighted() -> None:
    with pytest.raises(ValidationError):
        Ruleset.model_validate({"edge_weights": {"STACK_MEMBER": 0.5}})


@pytest.mark.parametrize(
    "override",
    [
        {"edge_weights": {"SAME_VPC": 1.5}},
        {"edge_weights": {"SAME_VPC": -0.1}},
        {"attach_threshold": 1.1},
        {"attach_margin": -0.01},
        {"confidence_high_declared_ratio": 2.0},
        {"confidence_medium_declared_ratio": -1.0},
    ],
)
def test_weights_and_thresholds_must_be_within_unit_interval(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Ruleset.model_validate(override)


def test_medium_ratio_cannot_exceed_high_ratio() -> None:
    with pytest.raises(ValidationError):
        Ruleset(confidence_high_declared_ratio=0.4, confidence_medium_declared_ratio=0.5)


def test_hash_is_stable_across_runs() -> None:
    assert Ruleset().content_hash() == Ruleset().content_hash()
    assert len(Ruleset().content_hash()) == 64


def test_changing_any_weight_changes_the_hash() -> None:
    base = Ruleset()
    for edge_type in sorted(base.edge_weights):
        weights = dict(base.edge_weights)
        weights[edge_type] = round((weights[edge_type] + 0.05) % 1.0, 4)
        changed = base.model_copy(update={"edge_weights": weights})
        assert changed.content_hash() != base.content_hash(), edge_type


def test_key_order_in_input_json_does_not_change_the_hash() -> None:
    data = Ruleset().model_dump(mode="json")
    reversed_data = dict(reversed(list(data.items())))
    reversed_data["edge_weights"] = dict(reversed(list(data["edge_weights"].items())))
    a = Ruleset.model_validate_json(json.dumps(data))
    b = Ruleset.model_validate_json(json.dumps(reversed_data))
    assert a.content_hash() == b.content_hash()


def test_load_and_save_round_trip(tmp_path: Path) -> None:
    rs = Ruleset(attach_threshold=0.55)
    path = tmp_path / "ruleset.json"
    rs.save(path)
    loaded = Ruleset.load(path)
    assert loaded == rs
    assert loaded.content_hash() == rs.content_hash()


def test_weight_lookup_defaults_missing_types_to_zero() -> None:
    rs = Ruleset.model_validate({"edge_weights": {"LB_TARGET": 0.8}})
    assert rs.weight(EdgeType.SAME_VPC) == 0.0
    assert rs.weight(EdgeType.LB_TARGET) == 0.8


def test_workload_tag_keys_are_lowercased_and_sorted() -> None:
    rs = Ruleset(workload_tag_keys=["Service", "app"])
    assert rs.workload_tag_keys == ["app", "service"]
