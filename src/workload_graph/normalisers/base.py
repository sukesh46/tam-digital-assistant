"""Normaliser output container and shared helpers.

Status: real
Purpose: ``NormalisedBatch`` plus a ``BatchBuilder`` that merges duplicates and sorts output,
    and helpers for walking snapshot pages with JSON-pointer evidence references.
Inputs: ``RawSnapshot``.
Outputs: ``NormalisedBatch``.
Planned dependencies: pydantic.
Notes: normalisers are pure functions; nothing here does I/O.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from workload_graph.models import Edge, Evidence, RawSnapshot, Resource
from workload_graph.models.evidence import MAX_DETAIL_LENGTH
from workload_graph.ruleset import Ruleset


class NormalisedBatch(BaseModel):
    """Resources, edges and notes produced from one snapshot, in sorted order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resources: tuple[Resource, ...] = ()
    edges: tuple[Edge, ...] = ()
    notes: tuple[str, ...] = ()


class Normaliser(Protocol):
    """A pure function from a snapshot to a batch."""

    def __call__(self, snapshot: RawSnapshot, ruleset: Ruleset) -> NormalisedBatch: ...


class BatchBuilder:
    """Accumulates resources and edges, merging duplicates; ``build()`` sorts everything."""

    def __init__(self) -> None:
        self._resources: dict[str, Resource] = {}
        self._edges: dict[tuple[str, str, str], Edge] = {}
        self._notes: set[str] = set()

    def add_resource(self, resource: Resource) -> None:
        """Add or merge a resource."""
        key = resource.key.key_str()
        existing = self._resources.get(key)
        self._resources[key] = existing.merged_with(resource) if existing else resource

    def add_edge(self, edge: Edge) -> None:
        """Add or merge an edge."""
        identity = edge.identity()
        existing = self._edges.get(identity)
        self._edges[identity] = existing.merged_with(edge) if existing else edge

    def note(self, text: str) -> None:
        """Record a note (deduplicated)."""
        self._notes.add(text)

    def has_resource(self, key_str: str) -> bool:
        """True if a resource with this key string was added."""
        return key_str in self._resources

    def build(self) -> NormalisedBatch:
        """Return a batch with resources, edges and notes in stable sorted order."""
        return NormalisedBatch(
            resources=tuple(self._resources[k] for k in sorted(self._resources)),
            edges=tuple(self._edges[k] for k in sorted(self._edges)),
            notes=tuple(sorted(self._notes)),
        )


def evidence(
    snapshot: RawSnapshot, pointer: str, detail: str, observed_at: datetime | None = None
) -> Evidence:
    """Evidence pointing at ``pointer`` inside ``snapshot``."""
    return Evidence(
        source=snapshot.source,
        observed_at=observed_at or snapshot.collected_at,
        raw_ref=f"{snapshot.snapshot_id}#{pointer}",
        detail=detail[:MAX_DETAIL_LENGTH],
    )


def iter_items(
    snapshot: RawSnapshot, operation: str, list_key: str
) -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Yield ``(json_pointer, item, page)`` for every item of ``list_key`` in every page.

    Any: items are raw boto3 response dicts.
    """
    for page_index, page in enumerate(snapshot.payload.get(operation, [])):
        items = page.get(list_key) or []
        for item_index, item in enumerate(items):
            if isinstance(item, dict):
                pointer = f"/payload/{operation}/{page_index}/{list_key}/{item_index}"
                yield pointer, item, page


def tags_to_dict(tags: Any) -> dict[str, str]:
    """Convert a boto3 ``[{"Key": k, "Value": v}]`` list to a dict. Any: raw boto3 value."""
    result: dict[str, str] = {}
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict) and isinstance(tag.get("Key"), str):
                result[tag["Key"]] = str(tag.get("Value", ""))
    return result
