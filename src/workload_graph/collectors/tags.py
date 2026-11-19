"""Tags collector (Resource Groups Tagging API ``GetResources``).

Status: real
Purpose: collect resource tag mappings for one account and region.
Inputs: client factory, scope.
Outputs: ``RawSnapshot`` with ``GetResources`` pages.
Planned dependencies: boto3.
Notes: # ASSUMPTION: GetResources only lists resources that are or were tagged; never-tagged
    resources are seen through other sources.
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


class TagsCollector(BaseCollector):
    """Collects ``GetResources`` pages."""

    source: ClassVar[SourceName] = "tags"
    allowed_operations: ClassVar[frozenset[str]] = frozenset({"GetResources"})

    def _run(self, recorder: CallRecorder, client_factory: ClientFactory, account_id: str,
             region: str, scope: CollectionScope) -> None:
        client = client_factory("resourcegroupstaggingapi", account_id, region)

        def get_resources() -> None:
            for page in recorder.paginate(client, "GetResources", {"ResourcesPerPage": 100},
                                          "PaginationToken", "PaginationToken"):
                recorder.record("GetResources", page)

        recorder.step("GetResources", get_resources)
