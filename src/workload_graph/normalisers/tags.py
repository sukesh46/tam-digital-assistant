"""Tags normaliser (Resource Groups Tagging API ``GetResources``).

Status: real
Purpose: turn tag mappings into resources and ``TAG_WORKLOAD_KEY`` edges.
Inputs: ``RawSnapshot`` with ``GetResources`` pages; ``Ruleset.workload_tag_keys``.
Outputs: ``NormalisedBatch``.
Planned dependencies: none.
Notes:
    - Tag keys are matched case-insensitively; values are compared exactly.
    - Each ``(lowercased key, value)`` group is linked as a star to its smallest member key
      (connectivity without quadratic edge growth). A group of one gets a self-edge so the
      declaration is not lost.
    - A resource with two different values across workload tag keys is a conflict: it is
      flagged (``tag_conflict``), noted, and produces no edges.
"""

from __future__ import annotations

from workload_graph.ids import key_for_arn, resource_type_from_arn
from workload_graph.models import Edge, EdgeType, Evidence, RawSnapshot, Resource
from workload_graph.normalisers.base import (
    BatchBuilder,
    NormalisedBatch,
    evidence,
    iter_items,
    tags_to_dict,
)
from workload_graph.ruleset import Ruleset

TAG_DETAIL_PREFIX = "workload tag "


def normalise_tags(snapshot: RawSnapshot, ruleset: Ruleset) -> NormalisedBatch:
    """Normalise a tags snapshot."""
    builder = BatchBuilder()
    workload_keys = set(ruleset.workload_tag_keys)
    groups: dict[tuple[str, str], dict[str, tuple[Resource, Evidence]]] = {}

    for pointer, item, _page in iter_items(snapshot, "GetResources", "ResourceTagMappingList"):
        arn = item.get("ResourceARN")
        if not isinstance(arn, str) or not arn:
            builder.note(f"tags: skipped mapping without ResourceARN at {pointer}")
            continue
        key = key_for_arn(arn, snapshot.account_id, snapshot.region)
        tags = tags_to_dict(item.get("Tags"))
        declared = {
            (k.lower(), v) for k, v in tags.items() if k.lower() in workload_keys and v
        }
        values = {v for _, v in declared}
        attributes: dict[str, str] = {}
        if len(values) > 1:
            attributes["tag_conflict"] = "true"
            builder.note(
                f"tags: workload tag conflict on {arn}: values {sorted(values)}; "
                "excluded from tag groups"
            )
        resource = Resource(
            key=key,
            resource_type=resource_type_from_arn(arn),
            name=tags.get("Name"),
            tags=tags,
            attributes=attributes,
            sources=frozenset({"tags"}),
        )
        builder.add_resource(resource)
        if len(values) > 1:
            continue
        for tag_key, value in sorted(declared):
            ev = evidence(snapshot, pointer, f"{TAG_DETAIL_PREFIX}{tag_key}={value}")
            groups.setdefault((tag_key, value), {})[key.key_str()] = (resource, ev)

    for (_tag_key, _value), members in sorted(groups.items()):
        ordered = [members[k] for k in sorted(members)]
        hub = ordered[0][0].key
        if len(ordered) == 1:
            builder.add_edge(Edge(src=hub, dst=hub, edge_type=EdgeType.TAG_WORKLOAD_KEY,
                                  evidence=(ordered[0][1],)))
            continue
        for resource, ev in ordered[1:]:
            builder.add_edge(Edge(src=resource.key, dst=hub,
                                  edge_type=EdgeType.TAG_WORKLOAD_KEY, evidence=(ev,)))
    return builder.build()
