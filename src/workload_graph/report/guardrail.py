"""Narrative guardrail (Section 10.11).

Status: real
Purpose: reject any sentence that is uncited, cites an unknown finding, states a number the cited
    findings do not hold, makes a causal claim, names an unknown workload, or is too long.
Inputs: a ``Narrative`` and the ``ReportCore`` it describes.
Outputs: ``check_narrative`` -> ``GuardrailResult`` with violation codes G1 to G6.
Planned dependencies: report.models.
Notes:
    - Dates and finding ids are ignored. Money and percentages must match a cited finding's
      rendered values. Then account ids in scope, workload names and the non-numeric string
      values of the cited findings (service names such as "Route 53") are removed, and any
      remaining integer of two or more digits must be an integer value of a cited finding.
    - "driven by" is allowed only when every cited finding is a ``spend_driver``: there it ranks
      contribution and claims no cause. Every other causal marker is blocked (F-D14).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

from workload_graph.findings.models import Finding, FindingKind
from workload_graph.report.models import Narrative, ReportCore, Sentence

MAX_SENTENCE = 400
CAUSAL = ("because", "due to", "caused by", "as a result of", "thanks to")
_MONEY = re.compile(r"-?\b[A-Z]{3} -?[0-9][0-9,]*(?:\.[0-9]+)?")
_PCT = re.compile(r"-?\b[0-9]+(?:\.[0-9]+)?%")
_INT = re.compile(r"\b[0-9]{2,}\b")
_EXEMPT = (re.compile(r"\b[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?\b"), re.compile(r"\bF-[0-9a-f]+\b"))
_NAMED = re.compile(r'workload "([^"]+)"')


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    section: str
    sentence: int
    code: str


class GuardrailResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    violations: tuple[Violation, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations

    def codes_for(self, section: str) -> tuple[str, ...]:
        """Sorted distinct codes raised in one section."""
        return tuple(sorted({v.code for v in self.violations if v.section == section}))


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
    except InvalidOperation:
        return False
    return True


def _sentence_codes(sentence: Sentence, core: ReportCore) -> list[str]:
    codes: list[str] = []
    if not sentence.finding_ids:
        codes.append("G1_NO_CITATION")
    cited: list[Finding] = []
    for finding_id in sentence.finding_ids:
        finding = core.finding(finding_id)
        if finding is None:
            codes.append("G2_UNKNOWN_FINDING")
        else:
            cited.append(finding)

    text = sentence.text
    names = sorted({w.name_hint for w in core.workloads}, key=len, reverse=True)
    for named in _NAMED.findall(text):
        if named not in names:
            codes.append("G5_UNKNOWN_WORKLOAD")
    stripped = text
    for pattern in _EXEMPT:
        stripped = pattern.sub(" ", stripped)
    allowed: set[str] = set()
    integers: set[str] = set()
    for finding in cited:
        rendered = finding.rendered_values()
        allowed |= rendered
        integers |= {r for r in rendered if r.isdigit()}
    mismatch = False
    for match in _MONEY.findall(stripped):
        mismatch |= match not in allowed
    stripped = _MONEY.sub(" ", stripped)
    for match in _PCT.findall(stripped):
        mismatch |= match not in allowed
    stripped = _PCT.sub(" ", stripped)
    for account in core.scope.accounts:
        stripped = stripped.replace(account, " ")
    for name in names:
        stripped = stripped.replace(name, " ")
    for finding in cited:
        for value in sorted((v for v in finding.values.values()
                             if isinstance(v, str) and len(v) >= 3 and not _is_number(v)),
                            key=len, reverse=True):
            stripped = stripped.replace(value, " ")
    for match in _INT.findall(stripped):
        mismatch |= match not in integers
    if mismatch:
        codes.append("G3_NUMBER_MISMATCH")

    lowered = text.lower()
    causal = any(re.search(rf"\b{re.escape(m)}\b", lowered) for m in CAUSAL)
    if "driven by" in lowered and not (cited and all(
            f.kind is FindingKind.SPEND_DRIVER for f in cited)):
        causal = True
    if causal and any(not f.supports_causal_claim for f in cited):
        codes.append("G4_CAUSAL_CLAIM")
    if causal and not cited:
        codes.append("G4_CAUSAL_CLAIM")
    if len(text) > MAX_SENTENCE:
        codes.append("G6_TOO_LONG")
    return codes


def check_narrative(narrative: Narrative, core: ReportCore) -> GuardrailResult:
    """Every violation in every section."""
    violations: list[Violation] = []
    for section in narrative.sections:
        for index, sentence in enumerate(section.sentences):
            for code in sorted(set(_sentence_codes(sentence, core))):
                violations.append(Violation(section=section.name, sentence=index, code=code))
    return GuardrailResult(violations=tuple(violations))
