"""Data-quality findings (Section 10.9).

Status: real
Purpose: say what could not be seen and why, each with the evidence that revealed it.
Inputs: per-label call statuses, tag keys seen in cost data, the tag keys workloads declare,
    stale recommendation counts.
Outputs: ``dq_finding`` (shared helper), ``source_findings``, ``tag_visibility_findings``,
    ``stale_findings``.
Planned dependencies: findings.models.
Notes: a finding that describes an absence carries the evidence of the response or the call
    record (``/payload/_labels/<n>``) that showed it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from workload_graph.cost.labels import LABELS_KEY, RESOURCE_COSTS_PREFIX
from workload_graph.findings.models import (
    RULE_VERSION,
    Confidence,
    Finding,
    FindingKind,
    Value,
)
from workload_graph.models import Evidence, RawSnapshot
from workload_graph.normalisers.base import evidence

UNAVAILABLE = ("denied", "not_enabled", "partial", "error")


def dq_finding(rule_id: str, account_id: str | None, values: Mapping[str, Value],
               evidence_items: Iterable[Evidence], subject: Mapping[str, str],
               workload_id: str | None = None,
               confidence: Confidence = Confidence.HIGH) -> Finding:
    """A data-quality finding."""
    return Finding.create(kind=FindingKind.DATA_QUALITY, rule_id=rule_id,
                          rule_version=RULE_VERSION, workload_id=workload_id,
                          account_id=account_id, confidence=confidence, values=values,
                          evidence=tuple(evidence_items), subject=subject)


def label_evidence(snapshot: RawSnapshot) -> dict[str, tuple[str, str, Evidence]]:
    """For each label: ``(status, code, evidence pointing at its call record)``."""
    found: dict[str, tuple[str, str, Evidence]] = {}
    for index, entry in enumerate(snapshot.payload.get(LABELS_KEY, [])):
        label = str(entry.get("label", ""))
        found[label] = (str(entry.get("status", "")), str(entry.get("code", "")),
                        evidence(snapshot, f"/payload/{LABELS_KEY}/{index}",
                                 f"{label}: {entry.get('status', '')}"))
    return found


def source_findings(snapshots: Sequence[RawSnapshot]) -> list[Finding]:
    """``source_unavailable`` per source, access account and status; and
    ``resource_level_not_enabled`` when a resource-level call reported ``not_enabled``."""
    findings: list[Finding] = []
    for snapshot in sorted(snapshots, key=lambda s: s.snapshot_id):
        labels = label_evidence(snapshot)
        by_status: dict[str, list[str]] = {}
        for label in sorted(labels):
            status = labels[label][0]
            if status in UNAVAILABLE:
                by_status.setdefault(status, []).append(label)
        for status in sorted(by_status):
            names = by_status[status]
            codes = sorted({labels[n][1] for n in names if labels[n][1]})
            findings.append(dq_finding(
                "source_unavailable", snapshot.account_id,
                {"source": snapshot.source, "status": status, "labels": ",".join(names),
                 "label_count": len(names), "codes": ",".join(codes)},
                [labels[n][2] for n in names],
                {"source": snapshot.source, "status": status}))
        resource = sorted(n for n in labels if n.startswith(RESOURCE_COSTS_PREFIX)
                          and labels[n][0] == "not_enabled")
        if resource:
            services = [n.removeprefix(RESOURCE_COSTS_PREFIX) for n in resource]
            findings.append(dq_finding(
                "resource_level_not_enabled", snapshot.account_id,
                {"services": ",".join(services)}, [labels[n][2] for n in resource],
                {"source": snapshot.source}))
    return findings


def tag_visibility_findings(declared_keys: Mapping[str, Sequence[str]],
                            tag_keys_seen: Iterable[str],
                            tag_call_evidence: Mapping[str, Evidence]) -> list[Finding]:
    """``tag_cost_not_visible`` for each tag key a workload declares that has no cost data.

    ``declared_keys`` maps a tag key to the workload ids that declare it. ``tag_call_evidence``
    maps an access account to the evidence of its ``GetTags`` response.
    """
    seen = {key.lower() for key in tag_keys_seen}
    findings: list[Finding] = []
    for key in sorted(declared_keys):
        if key.lower() in seen or not tag_call_evidence:
            continue
        workloads = sorted(set(declared_keys[key]))
        findings.append(dq_finding(
            "tag_cost_not_visible", None,
            {"tag_key": key, "workloads": ",".join(workloads), "workload_count": len(workloads),
             "explanation": "the tag key has no cost data: it is inactive or unused as a cost "
                            "allocation tag"},
            [tag_call_evidence[a] for a in sorted(tag_call_evidence)], {"tag_key": key.lower()}))
    return findings


def stale_findings(stale: Mapping[str, list[Evidence]], days: int) -> list[Finding]:
    """One ``stale_recommendations`` finding per account with a count."""
    return [dq_finding("stale_recommendations", account,
                       {"count": len(stale[account]), "older_than_days": days},
                       stale[account], {"account": account})
            for account in sorted(stale) if stale[account]]
