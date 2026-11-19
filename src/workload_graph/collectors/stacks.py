"""CloudFormation stacks collector.

Status: real
Purpose: collect stacks (``ListStacks``) and the resources of every live stack
    (``DescribeStackResources``).
Inputs: client factory, scope.
Outputs: ``RawSnapshot``.
Planned dependencies: boto3.
Notes: # ASSUMPTION: DescribeStackResources returns at most 100 resources per stack; larger
    stacks need ListStackResources (not yet implemented, see docs/OPEN_QUESTIONS.md).
"""

from __future__ import annotations

from typing import ClassVar

from workload_graph.collectors.base import (
    BaseCollector,
    CallRecorder,
    ClientFactory,
    CollectionScope,
)
from workload_graph.models import SourceName


class StacksCollector(BaseCollector):
    """Collects stacks and their resources."""

    source: ClassVar[SourceName] = "stacks"
    allowed_operations: ClassVar[frozenset[str]] = frozenset(
        {"ListStacks", "DescribeStackResources"})

    def _run(self, recorder: CallRecorder, client_factory: ClientFactory, account_id: str,
             region: str, scope: CollectionScope) -> None:
        client = client_factory("cloudformation", account_id, region)
        live: set[str] = set()

        def list_stacks() -> None:
            for page in recorder.paginate(client, "ListStacks", {}, "NextToken", "NextToken"):
                recorder.record("ListStacks", page)
                for summary in page.get("StackSummaries") or []:
                    if summary.get("StackStatus") != "DELETE_COMPLETE":
                        live.add(str(summary.get("StackId")))

        if not recorder.step("ListStacks", list_stacks):
            return
        for stack_id in sorted(live):
            def describe(stack_id: str = stack_id) -> None:
                page = recorder.call(client, "DescribeStackResources", StackName=stack_id)
                recorder.record("DescribeStackResources", page)

            if not recorder.step(f"DescribeStackResources {stack_id}", describe):
                return
