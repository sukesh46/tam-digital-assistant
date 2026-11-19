"""Pairwise precision, recall, F1 and coverage.

Status: real
Purpose: measure a build against a human-written golden scenario.
Inputs: ``BuildResult``, ``GoldenScenario``.
Outputs: ``EvaluationReport``.
Planned dependencies: none.
Notes:
    - A pair is two resources placed in the same workload. Shared infrastructure is removed
      from those sets and listed on the report instead.
    - Coverage and the unmapped rate use every placed resource except shared infrastructure
      as the denominator.
    - No predicted pairs means there are no false positives, so precision is 1.0. Recall of
      an empty prediction against a non-empty golden file is 0.0.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from workload_graph.evaluation.golden import GoldenScenario
from workload_graph.models import BuildResult


class EvaluationReport(BaseModel):
    """Scores for one build against one golden scenario."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pairwise_precision: float
    pairwise_recall: float
    pairwise_f1: float
    coverage: float
    unmapped_rate: float
    predicted_pair_count: int
    golden_pair_count: int
    matched_pair_count: int
    shared_infrastructure: list[str]

    def summary(self) -> str:
        """A short printable report."""
        lines = [
            f"pairwise precision: {self.pairwise_precision:.4f}",
            f"pairwise recall: {self.pairwise_recall:.4f}",
            f"pairwise f1: {self.pairwise_f1:.4f}",
            f"coverage: {self.coverage:.4f}",
            f"unmapped rate: {self.unmapped_rate:.4f}",
            f"shared infrastructure: {len(self.shared_infrastructure)}",
            "pairs predicted/golden/matched: "
            f"{self.predicted_pair_count}/{self.golden_pair_count}/{self.matched_pair_count}",
        ]
        return "\n".join(lines) + "\n"


def placed_key_strings(result: BuildResult) -> set[str]:
    """Every resource key placed by a build: workload, shared, residual or unmapped."""
    keys = {member.key.key_str() for workload in result.workloads for member in workload.members}
    keys.update(item.key.key_str() for item in result.shared_infrastructure)
    keys.update(
        member.key.key_str() for group in result.residual_groups for member in group.members
    )
    keys.update(item.key.key_str() for item in result.unmapped)
    return keys


def _pairs(groups: Iterable[Iterable[str]]) -> set[frozenset[str]]:
    pairs: set[frozenset[str]] = set()
    for group in groups:
        members = sorted(set(group))
        for index, left in enumerate(members):
            for right in members[index + 1:]:
                pairs.add(frozenset((left, right)))
    return pairs


def _ratio(matched: int, total: int) -> float:
    """``matched / total``, or 1.0 when ``total`` is 0 (nothing to get wrong)."""
    if total == 0:
        return 1.0
    return matched / total


def evaluate(build_result: BuildResult, golden: GoldenScenario) -> EvaluationReport:
    """Score ``build_result`` against ``golden``."""
    shared = sorted(item.key.key_str() for item in build_result.shared_infrastructure)
    predicted_groups = [
        [member.key.key_str() for member in workload.members if member.key.key_str() not in shared]
        for workload in build_result.workloads
    ]
    golden_skip = set(golden.shared)
    golden_groups = [
        [member for member in workload.members if member not in golden_skip]
        for workload in golden.workloads
    ]
    predicted_pairs = _pairs(predicted_groups)
    golden_pairs = _pairs(golden_groups)
    matched = predicted_pairs & golden_pairs
    precision = _ratio(len(matched), len(predicted_pairs))
    recall = _ratio(len(matched), len(golden_pairs))
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)

    eligible = placed_key_strings(build_result) - set(shared)
    in_workload = {key for group in predicted_groups for key in group}
    unmapped = {item.key.key_str() for item in build_result.unmapped}
    coverage = _ratio(len(in_workload), len(eligible))
    unmapped_rate = 0.0 if not eligible else len(unmapped) / len(eligible)
    return EvaluationReport(
        pairwise_precision=precision,
        pairwise_recall=recall,
        pairwise_f1=f1,
        coverage=coverage,
        unmapped_rate=unmapped_rate,
        predicted_pair_count=len(predicted_pairs),
        golden_pair_count=len(golden_pairs),
        matched_pair_count=len(matched),
        shared_infrastructure=shared,
    )
