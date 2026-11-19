"""The structured, cited findings report (Section 7.4).

Status: real
Purpose: the one artefact the cost layer produces. Every statement cites findings; every finding
    cites raw responses.
Inputs: assembled by ``report.assemble``.
Outputs: ``FindingsReport`` and its parts; ``Narrative``, ``Sentence``.
Planned dependencies: pydantic, findings.
Notes: serialised with sorted keys, Decimals as strings and a trailing newline. ``ReportCore`` is
    the same model with an empty narrative; the narrator reads it.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from workload_graph.findings.asks import Ask, TalkingPoint
from workload_graph.findings.attribution import BucketTable, WorkloadSpend
from workload_graph.findings.models import Confidence, Finding

SCHEMA_VERSION = "1"
SECTION_NAMES = ("summary", "account_spend", "workloads", "savings", "data_coverage", "asks")
Coverage = Literal["available", "empty", "denied", "not_enabled", "partial", "error"]


class Sentence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    finding_ids: tuple[str, ...]


class NarrativeSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    sentences: tuple[Sentence, ...] = ()


class Narrative(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sections: tuple[NarrativeSection, ...] = ()

    def section(self, name: str) -> NarrativeSection:
        """The named section, or an empty one."""
        for section in self.sections:
            if section.name == name:
                return section
        return NarrativeSection(name=name)


class GeneratedFrom(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    build_result_sha256: str
    workload_ruleset_hash: str
    findings_ruleset_hash: str
    snapshot_ids: tuple[str, ...]
    as_of: str


class ReportScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    accounts: tuple[str, ...]
    months: tuple[str, ...]
    currency: str | None
    metric: str


class CoverageEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str
    source: str
    status: Coverage
    labels: dict[str, str] = {}


class CoverageReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[CoverageEntry, ...] = ()
    tag_cost_visible: bool = False
    resource_level_visible: bool = False


class SummaryFigure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: str | None
    unit: str | None
    finding_ids: tuple[str, ...] = ()
    note: str = ""


class WorkloadSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workload_id: str
    name_hint: str
    confidence: Confidence
    accounts: tuple[str, ...]
    spend: tuple[WorkloadSpend, ...]
    savings_sum_of_resource_recommendations: str | None
    findings: tuple[str, ...]


class EvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    snapshot_id: str
    pointer: str
    detail: str
    observed_at: str


class FindingsReport(BaseModel):
    """The report. ``narrative`` is empty in the core handed to a narrator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = SCHEMA_VERSION
    generated_from: GeneratedFrom
    scope: ReportScope
    coverage: CoverageReport
    summary: tuple[SummaryFigure, ...]
    account_level: tuple[str, ...]
    workloads: tuple[WorkloadSection, ...]
    buckets: tuple[BucketTable, ...]
    customer_asks: tuple[Ask, ...]
    talking_points: tuple[TalkingPoint, ...]
    narrative: Narrative = Narrative()
    findings: tuple[Finding, ...]
    evidence_index: dict[str, tuple[EvidenceRef, ...]]
    notes: tuple[str, ...]

    def finding(self, finding_id: str) -> Finding | None:
        """A finding by id."""
        for item in self.findings:
            if item.finding_id == finding_id:
                return item
        return None

    def to_json(self) -> str:
        """Sorted keys, Decimals as strings, trailing newline (byte-stable)."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"


ReportCore = FindingsReport
