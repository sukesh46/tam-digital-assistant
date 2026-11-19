"""Cost Explorer normaliser (pure).

Status: real
Purpose: turn Cost Explorer pages into ``CostFact`` records with the dimensions the response
    itself declares (Section 10.3).
Inputs: a ``cost_explorer`` ``RawSnapshot`` whose payload keys are ``"<Operation>:<label>"``.
Outputs: ``CostExplorerBatch`` (facts, notes, tag keys seen, per-label status).
Planned dependencies: cost.money, cost.labels, cost.models.
Notes:
    - Group keys are mapped by the response's ``GroupDefinitions``, never by the request.
    - Malformed rows are skipped with a note, never an exception.
    - Daily account calls yield one ``account_window_total`` per account (the sum of the days).
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from workload_graph.cost.labels import (
    LABELS_KEY,
    RESOURCE_COSTS_PREFIX,
    SPEND_BY_SERVICE,
    TAG_KEYS_SEEN,
    TAG_SPEND_PREFIX,
    WINDOW_TOTAL,
    split_payload_key,
)
from workload_graph.cost.models import CostFact, CostFactKind
from workload_graph.cost.money import parse_amount
from workload_graph.cost.periods import Period
from workload_graph.cost.snapshots import escape_pointer
from workload_graph.models import Evidence, RawSnapshot
from workload_graph.normalisers.base import evidence

DEFAULT_METRIC = "UnblendedCost"


class CostExplorerBatch(BaseModel):
    """Facts and context from one Cost Explorer snapshot, in sorted order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    facts: tuple[CostFact, ...] = ()
    notes: tuple[str, ...] = ()
    tag_keys_seen: tuple[str, ...] = ()
    label_status: dict[str, str] = {}


def split_tag_group_key(raw: str) -> tuple[str, str]:
    """Split a tag group key into ``(key, value)``; an empty value means untagged.

    # ASSUMPTION: tag group keys look like ``key$value`` and untagged like ``key$``
    # (assumption 1 in docs/ASSUMPTIONS.md).
    """
    key, _, value = raw.partition("$")
    return key, value


def label_statuses(snapshot: RawSnapshot) -> dict[str, str]:
    """Status per call label: from ``_labels``, else ``ok`` for every label with pages."""
    statuses: dict[str, str] = {}
    for key in sorted(snapshot.payload):
        if not key.startswith("_"):
            statuses[split_payload_key(key)[1]] = "ok"
    for entry in snapshot.payload.get(LABELS_KEY, []):
        label = entry.get("label")
        if isinstance(label, str):
            statuses[label] = str(entry.get("status", "error"))
    return {label: statuses[label] for label in sorted(statuses)}


def _fact_id(account_id: str, label: str, kind: str, dims: dict[str, str], period: Period) -> str:
    text = "|".join([account_id, label, kind, period.label()]
                    + [f"{k}={dims[k]}" for k in sorted(dims)])
    return "C-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _dimensions(definitions: Any, keys: Any) -> dict[str, str] | None:
    if not isinstance(definitions, list) or not isinstance(keys, list):
        return None
    if len(definitions) != len(keys):
        return None
    dims: dict[str, str] = {}
    for definition, raw in zip(definitions, keys, strict=True):
        if not isinstance(definition, dict) or not isinstance(raw, str):
            return None
        kind = definition.get("Type")
        if kind == "TAG":
            tag_key, tag_value = split_tag_group_key(raw)
            dims["tag_key"] = str(definition.get("Key") or tag_key)
            dims["tag_value"] = tag_value
        else:
            dims[str(definition.get("Key", "")).lower()] = raw
    return dims


def _period(value: Any) -> Period:
    if not isinstance(value, dict):
        raise ValueError("no time period")
    return Period(start=date.fromisoformat(str(value.get("Start"))),
                  end=date.fromisoformat(str(value.get("End"))))


class _Window:
    def __init__(self) -> None:
        self.amount = Decimal(0)
        self.unit: str | None = None
        self.start: date | None = None
        self.end: date | None = None
        self.estimated = False
        self.mixed = False


def top_services(pages: list[dict[str, Any]], month: Period, n: int,
                 metric: str = DEFAULT_METRIC) -> list[str]:
    """The ``n`` services with the highest spend in ``month``, highest first (ties by name)."""
    totals: dict[str, Decimal] = {}
    for page in pages:
        definitions = page.get("GroupDefinitions")
        for row in page.get("ResultsByTime") or []:
            try:
                if not isinstance(row, dict) or _period(row.get("TimePeriod")) != month:
                    continue
            except ValueError:
                continue
            for group in row.get("Groups") or []:
                dims = _dimensions(definitions,
                                   group.get("Keys") if isinstance(group, dict) else None)
                entry = (group.get("Metrics") or {}).get(metric) if dims else None
                if dims is None or not isinstance(entry, dict) or "service" not in dims:
                    continue
                try:
                    amount = parse_amount(entry.get("Amount"))
                except ValueError:
                    continue
                totals[dims["service"]] = totals.get(dims["service"], Decimal(0)) + amount
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return [service for service, amount in ranked if amount > 0][:n]


def normalise_cost_explorer(snapshot: RawSnapshot,
                            metric: str = DEFAULT_METRIC) -> CostExplorerBatch:
    """Normalise one Cost Explorer snapshot."""
    facts: dict[str, CostFact] = {}
    notes: set[str] = set()
    tag_keys: set[str] = set()
    windows: dict[str, _Window] = {}
    window_refs: list[Evidence] = []

    for key in sorted(snapshot.payload):
        if key.startswith("_"):
            continue
        _, label = split_payload_key(key)
        pages = snapshot.payload[key]
        if label == TAG_KEYS_SEEN:
            for page in pages:
                tag_keys.update(t for t in page.get("Tags") or [] if isinstance(t, str))
            continue
        kind: CostFactKind
        extra: dict[str, str] = {}
        if label == SPEND_BY_SERVICE:
            kind = "account_service_month"
        elif label.startswith(TAG_SPEND_PREFIX):
            kind = "tag_month"
        elif label.startswith(RESOURCE_COSTS_PREFIX):
            kind = "resource_day"
            extra = {"service": label.removeprefix(RESOURCE_COSTS_PREFIX)}
        elif label == WINDOW_TOTAL:
            kind = "account_window_total"
            window_refs.append(evidence(snapshot, f"/payload/{escape_pointer(key)}",
                                        f"{label} daily totals"))
        else:
            notes.add(f"cost_explorer: unknown call label {label}; skipped")
            continue
        base = f"/payload/{escape_pointer(key)}"
        for page_index, page in enumerate(pages):
            definitions = page.get("GroupDefinitions")
            for row_index, row in enumerate(page.get("ResultsByTime") or []):
                try:
                    if not isinstance(row, dict):
                        raise ValueError("row is not an object")
                    period = _period(row.get("TimePeriod"))
                except ValueError:
                    notes.add(f"cost_explorer: {label} has a malformed row; skipped")
                    continue
                estimated = bool(row.get("Estimated", False))
                for group_index, group in enumerate(row.get("Groups") or []):
                    pointer = f"{base}/{page_index}/ResultsByTime/{row_index}/Groups/{group_index}"
                    dims = _dimensions(definitions,
                                       group.get("Keys") if isinstance(group, dict) else None)
                    if dims is None:
                        notes.add(f"cost_explorer: {label} has a group whose keys do not match "
                                  "its group definitions; skipped")
                        continue
                    metrics = group.get("Metrics") or {}
                    entry = metrics.get(metric) if isinstance(metrics, dict) else None
                    try:
                        if not isinstance(entry, dict):
                            raise ValueError("no metric")
                        amount = parse_amount(entry.get("Amount"))
                        unit = str(entry.get("Unit") or "")
                    except ValueError:
                        notes.add(f"cost_explorer: {label} has a group with no amount for "
                                  f"{metric}; skipped")
                        continue
                    if kind == "account_window_total":
                        account = dims.get("linked_account", snapshot.account_id)
                        window = windows.setdefault(account, _Window())
                        if window.unit not in (None, unit):
                            window.mixed = True
                        window.unit = window.unit or unit
                        window.amount += amount
                        window.start = min(filter(None, (window.start, period.start)))
                        window.end = max(filter(None, (window.end, period.end)))
                        window.estimated = window.estimated or estimated
                        continue
                    dims.update(extra)
                    fact_id = _fact_id(snapshot.account_id, label, kind, dims, period)
                    if fact_id in facts:
                        continue
                    detail = f"{label} {period.label()} " + ",".join(
                        f"{k}={dims[k]}" for k in sorted(dims))
                    facts[fact_id] = CostFact(
                        fact_id=fact_id, kind=kind, dimensions=dims, period=period,
                        amount=amount, unit=unit, estimated=estimated,
                        evidence=evidence(snapshot, pointer, detail))

    for account in sorted(windows):
        window = windows[account]
        if window.mixed:
            notes.add(f"cost_explorer: window total for {account} mixes currencies; skipped")
            continue
        if window.start is None or window.end is None or window.unit is None:
            continue
        period = Period(start=window.start, end=window.end)
        dims = {"linked_account": account}
        fact_id = _fact_id(snapshot.account_id, WINDOW_TOTAL, "account_window_total", dims, period)
        facts[fact_id] = CostFact(
            fact_id=fact_id, kind="account_window_total", dimensions=dims, period=period,
            amount=window.amount, unit=window.unit, estimated=window.estimated,
            evidence=window_refs[0])

    return CostExplorerBatch(
        facts=tuple(sorted(facts.values(), key=lambda f: f.sort_key())),
        notes=tuple(sorted(notes)),
        tag_keys_seen=tuple(sorted(tag_keys)),
        label_status=label_statuses(snapshot),
    )
