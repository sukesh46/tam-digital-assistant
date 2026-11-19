"""Evaluation against golden scenarios.

Status: real
Purpose: load golden files and score a build.
Inputs: none.
Outputs: ``load_golden``, ``evaluate``, ``EvaluationReport``.
Planned dependencies: none.
Notes: none.
"""

from workload_graph.evaluation.golden import GoldenScenario, GoldenWorkload, load_golden
from workload_graph.evaluation.metrics import EvaluationReport, evaluate, placed_key_strings

__all__ = [
    "EvaluationReport",
    "GoldenScenario",
    "GoldenWorkload",
    "evaluate",
    "load_golden",
    "placed_key_strings",
]
