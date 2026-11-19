"""Read-only collectors that turn AWS API responses into raw snapshots.

Status: real
Purpose: registry of collectors by source.
Inputs: none.
Outputs: ``COLLECTORS``.
Planned dependencies: boto3.
Notes: collectors are only exercised offline in tests; live wiring is a SKELETON (see cli).
"""

from workload_graph.collectors.base import BaseCollector
from workload_graph.collectors.cloudtrail import CloudTrailCollector
from workload_graph.collectors.stacks import StacksCollector
from workload_graph.collectors.tags import TagsCollector
from workload_graph.collectors.topology import TopologyCollector

COLLECTORS: dict[str, type[BaseCollector]] = {
    "cloudtrail": CloudTrailCollector,
    "stacks": StacksCollector,
    "tags": TagsCollector,
    "topology": TopologyCollector,
}

__all__ = [
    "COLLECTORS",
    "BaseCollector",
    "CloudTrailCollector",
    "StacksCollector",
    "TagsCollector",
    "TopologyCollector",
]
