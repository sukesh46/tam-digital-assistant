"""Step 11: determinism. These are the most important tests in the project."""

from __future__ import annotations

import hashlib
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from wg_test_helpers import (
    SCENARIOS,
    build_from_snapshots,
    scenario_snapshots,
    scenario_workspace,
)

from workload_graph.ruleset import Ruleset

pytestmark = pytest.mark.determinism
TESTS_DIR = Path(__file__).resolve().parents[1]

_BASELINES: dict[str, str] = {}


def _baseline(name: str) -> str:
    if name not in _BASELINES:
        result, _ = build_from_snapshots(scenario_snapshots(name), scenario_workspace(name))
        _BASELINES[name] = result.to_json()
    return _BASELINES[name]


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(name=st.sampled_from(SCENARIOS), seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_shuffling_inputs_yields_identical_serialised_result(name: str, seed: int) -> None:
    result, _ = build_from_snapshots(scenario_snapshots(name), scenario_workspace(name),
                                     rng=random.Random(seed))
    assert result.to_json() == _baseline(name)


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_scenario_survives_a_fixed_set_of_shuffles(name: str) -> None:
    for seed in range(3):
        result, _ = build_from_snapshots(scenario_snapshots(name), scenario_workspace(name),
                                         rng=random.Random(seed))
        assert result.to_json() == _baseline(name)


_SCRIPT = """
import hashlib, sys
sys.path.insert(0, sys.argv[1])
from wg_test_helpers import SCENARIOS, build_from_snapshots, scenario_snapshots, scenario_workspace
from wg_synthetic import synthetic_snapshots, synthetic_workspace
digest = hashlib.sha256()
for name in SCENARIOS:
    result, _ = build_from_snapshots(scenario_snapshots(name), scenario_workspace(name))
    digest.update(result.to_json().encode())
result, _ = build_from_snapshots(synthetic_snapshots(), synthetic_workspace())
digest.update(result.to_json().encode())
print(digest.hexdigest())
"""


@pytest.mark.slow
def test_output_is_identical_across_pythonhashseed_values() -> None:
    digests = set()
    for hash_seed in ("0", "1", "4242", "random"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        completed = subprocess.run(
            [sys.executable, "-c", _SCRIPT, str(TESTS_DIR)],
            capture_output=True, text=True, env=env, check=True, timeout=600,
        )
        digests.add(completed.stdout.strip())
    assert len(digests) == 1
    assert len(next(iter(digests))) == 64


@pytest.mark.parametrize("name", SCENARIOS)
def test_rerunning_gives_the_same_workload_ids(name: str) -> None:
    first, _ = build_from_snapshots(scenario_snapshots(name), scenario_workspace(name))
    second, _ = build_from_snapshots(scenario_snapshots(name), scenario_workspace(name))
    assert [w.workload_id for w in first.workloads] == [w.workload_id for w in second.workloads]


@pytest.mark.parametrize("name", SCENARIOS)
def test_changing_the_ruleset_changes_the_hash_but_not_identity_rules(name: str) -> None:
    base, _ = build_from_snapshots(scenario_snapshots(name), scenario_workspace(name))
    tweaked_rules = Ruleset(attach_threshold=0.65, attach_margin=0.1)
    tweaked, _ = build_from_snapshots(scenario_snapshots(name), scenario_workspace(name),
                                      ruleset=tweaked_rules)
    assert tweaked.ruleset_hash != base.ruleset_hash
    assert tweaked.ruleset_hash == tweaked_rules.content_hash()
    assert [w.workload_id for w in tweaked.workloads] == [w.workload_id for w in base.workloads]


def test_baselines_differ_between_scenarios() -> None:
    hashes = {hashlib.sha256(_baseline(n).encode()).hexdigest() for n in SCENARIOS}
    assert len(hashes) == len(SCENARIOS)
