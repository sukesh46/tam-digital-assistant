"""Golden scenario loader.

Status: real
Purpose: load the expected workloads for a scenario and reject references to unknown resources.
Inputs: a golden JSON file, optionally the resource keys known to exist.
Outputs: ``GoldenScenario``.
Planned dependencies: pydantic.
Notes: golden files are the human expectation. They are not generated from the builder.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class GoldenWorkload(BaseModel):
    """One expected workload, as a set of resource key strings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    members: list[str]


class GoldenScenario(BaseModel):
    """Expected workloads and shared infrastructure for one scenario."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: str
    description: str = ""
    workloads: list[GoldenWorkload]
    shared: list[str] = []

    def referenced_keys(self) -> list[str]:
        """Every resource key the file mentions, members first then shared."""
        keys = [member for workload in self.workloads for member in workload.members]
        keys.extend(self.shared)
        return keys


def load_golden(path: Path, known_keys: Collection[str] | None = None) -> GoldenScenario:
    """Load a golden file. With ``known_keys``, unknown resources are an error."""
    scenario = GoldenScenario.model_validate_json(Path(path).read_text(encoding="utf-8"))
    if known_keys is not None:
        known = set(known_keys)
        unknown = sorted({key for key in scenario.referenced_keys() if key not in known})
        if unknown:
            preview = ", ".join(unknown[:5])
            raise ValueError(f"golden references unknown resources: {preview}")
    return scenario
