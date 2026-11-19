"""Stacks normaliser (CloudFormation ``ListStacks`` and ``DescribeStackResources``).

Status: real
Purpose: emit each live stack as a resource and a ``STACK_MEMBER`` edge from every stack
    resource to its stack.
Inputs: ``RawSnapshot`` with ``ListStacks`` and ``DescribeStackResources`` pages.
Outputs: ``NormalisedBatch``.
Planned dependencies: none.
Notes:
    - ``DELETE_COMPLETE`` stacks and ``DELETE_COMPLETE`` stack resources are ignored.
    - Nested stacks: the child stack records ``parent_stack_id``; the parent does NOT get a
      ``STACK_MEMBER`` edge to the child, so the child's resources stay in the child group.
    - Physical ids are converted with ``arn_for``; unknown types keep an ``unresolved:`` key.
"""

from __future__ import annotations

from workload_graph.ids import arn_for, key_for_arn
from workload_graph.models import Edge, EdgeType, RawSnapshot, Resource
from workload_graph.normalisers.base import BatchBuilder, NormalisedBatch, evidence, iter_items
from workload_graph.ruleset import Ruleset

STACK_TYPE = "AWS::CloudFormation::Stack"
DELETED = "DELETE_COMPLETE"


def normalise_stacks(snapshot: RawSnapshot, ruleset: Ruleset) -> NormalisedBatch:
    """Normalise a stacks snapshot."""
    del ruleset  # no stack rules yet; kept for a uniform normaliser signature
    builder = BatchBuilder()
    live: dict[str, str] = {}  # stack id -> stack name

    for _pointer, summary, _ in iter_items(snapshot, "ListStacks", "StackSummaries"):
        stack_id, name = summary.get("StackId"), summary.get("StackName")
        status = str(summary.get("StackStatus", ""))
        if not isinstance(stack_id, str) or not isinstance(name, str) or status == DELETED:
            continue
        live[stack_id] = name
        attributes = {"stack_name": name, "stack_status": status}
        parent = summary.get("ParentId")
        if isinstance(parent, str) and parent:
            attributes["parent_stack_id"] = parent
            builder.note(f"stacks: nested stack {name} has parent {parent}")
        builder.add_resource(Resource(
            key=key_for_arn(stack_id, snapshot.account_id, snapshot.region),
            resource_type=STACK_TYPE,
            name=name,
            attributes=attributes,
            sources=frozenset({"stacks"}),
        ))

    for pointer, row, _ in iter_items(snapshot, "DescribeStackResources", "StackResources"):
        stack_id = row.get("StackId")
        if not isinstance(stack_id, str) or stack_id not in live:
            continue
        if row.get("ResourceStatus") == DELETED:
            continue
        stack_name = live[stack_id]
        logical = str(row.get("LogicalResourceId", ""))
        resource_type = str(row.get("ResourceType", ""))
        physical = row.get("PhysicalResourceId")
        if not isinstance(physical, str) or not physical:
            builder.note(f"stacks: {stack_name}/{logical} has no physical id; skipped")
            continue
        if resource_type == STACK_TYPE:
            # Nested child stack: its parent link comes from ListStacks.ParentId.
            continue
        resolution = arn_for(resource_type, physical, snapshot.account_id, snapshot.region)
        if resolution.note:
            builder.note(f"stacks: {stack_name}/{logical}: {resolution.note}")
        key = key_for_arn(resolution.arn, snapshot.account_id, snapshot.region)
        builder.add_resource(Resource(
            key=key,
            resource_type=resource_type,
            attributes={"stack_name": stack_name, "logical_id": logical},
            sources=frozenset({"stacks"}),
        ))
        builder.add_edge(Edge(
            src=key,
            dst=key_for_arn(stack_id, snapshot.account_id, snapshot.region),
            edge_type=EdgeType.STACK_MEMBER,
            evidence=(evidence(snapshot, pointer, f"member of stack {stack_name} as {logical}"),),
        ))
    return builder.build()
