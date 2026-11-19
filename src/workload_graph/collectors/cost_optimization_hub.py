"""Cost Optimization Hub collector (read-only).

Status: real
Purpose: list recommendations and per-account deduplicated savings for one access plan.
Inputs: client factory, ``AccessPlan``, ``CostScope``, ``FindingsRuleset``, the collection time.
Outputs: one ``cost_optimization_hub`` ``RawSnapshot``.
Planned dependencies: boto3 (through the factory), collectors.base, cost.
Notes:
    - # ASSUMPTION: the hub is called on the us-east-1 endpoint (assumption 10).
    - # ASSUMPTION: ``includeAllRecommendations=False`` returns one recommendation per resource
      (assumption 6).
    - # RISK: ``UpdateEnrollmentStatus`` and ``UpdatePreferences`` change customer settings.
      They are not in the allowlist and the forbidden-prefix rule refuses them as well.
"""

from __future__ import annotations

import time
from datetime import datetime
from functools import partial
from typing import Any, ClassVar

from workload_graph.collectors.base import CallRecorder, ClientFactory, CollectionScope, SleepFn
from workload_graph.collectors.cost_common import LabelledRun, cost_snapshot
from workload_graph.cost.labels import ENROLMENT, LIST_RECOMMENDATIONS, SUMMARY_PREFIX
from workload_graph.cost.models import AccessPlan, CostScope
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.models import RawSnapshot, SourceName

COH_REGION = "us-east-1"


class CostOptimizationHubCollector:
    """Collects enrolment, recommendations and deduplicated savings summaries."""

    source: ClassVar[SourceName] = "cost_optimization_hub"
    allowed_operations: ClassVar[frozenset[str]] = frozenset({
        "ListRecommendations", "ListRecommendationSummaries", "ListEnrollmentStatuses",
        "GetRecommendation", "GetPreferences"})

    def __init__(self, sleep: SleepFn = time.sleep) -> None:
        self._sleep = sleep

    def collect(self, client_factory: ClientFactory, plan: AccessPlan, scope: CostScope,
                ruleset: FindingsRuleset, collected_at: datetime) -> RawSnapshot:
        """Run every labelled call within ``scope.max_calls``. Never raises on denial."""
        recorder = CallRecorder(CollectionScope(now=collected_at, max_calls=scope.max_calls),
                                self._sleep, self.allowed_operations, ruleset.not_enabled)
        run = LabelledRun(recorder)
        client = client_factory("cost-optimization-hub", plan.access_account_id, COH_REGION)

        def paged(operation: str, label: str, params: dict[str, Any]) -> None:
            for page in recorder.paginate(client, operation, params, "nextToken", "nextToken"):
                run.record(operation, label, page)

        operations = set(client.meta.service_model.operation_names)
        if "ListEnrollmentStatuses" in operations:
            run.run("ListEnrollmentStatuses", ENROLMENT,
                    lambda: paged("ListEnrollmentStatuses", ENROLMENT, {}))
        else:
            run.skip("ListEnrollmentStatuses", ENROLMENT, "not in this botocore model")
        run.run("ListRecommendations", LIST_RECOMMENDATIONS, lambda: paged(
            "ListRecommendations", LIST_RECOMMENDATIONS,
            {"filter": {"accountIds": list(plan.linked_account_ids)},
             "includeAllRecommendations": False}))
        for account in plan.linked_account_ids:
            label = SUMMARY_PREFIX + account
            run.run("ListRecommendationSummaries", label, partial(
                paged, "ListRecommendationSummaries", label,
                {"groupBy": "ActionType", "filter": {"accountIds": [account]}}))

        return cost_snapshot(
            self.source, plan.access_account_id, collected_at, run,
            f"cost_optimization_hub-{plan.access_account_id}-{scope.as_of.isoformat()}")
