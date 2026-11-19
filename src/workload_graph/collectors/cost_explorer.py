"""Cost Explorer collector (read-only).

Status: real
Purpose: make the labelled Cost Explorer calls of Section 10.2 for one access plan.
Inputs: client factory, ``AccessPlan``, ``CostScope``, ``FindingsRuleset``, the collection time.
Outputs: one ``cost_explorer`` ``RawSnapshot`` keyed by ``"<Operation>:<label>"``.
Planned dependencies: boto3 (through the factory), collectors.base, cost.
Notes:
    - # ASSUMPTION: Cost Explorer is called on the us-east-1 endpoint (assumption 10).
    - # ASSUMPTION: GroupBy allows at most two definitions (assumption 2); each call uses two.
    - # ASSUMPTION: GetCostAndUsageWithResources needs a Filter (botocore marks it required) and a
      window of at most 14 days (assumption 3); the window is 13 days (F-D17).
    - # RISK: Cost Explorer charges per paginated request; the call budget bounds it.
    - After the first resource-level call reports ``not_enabled``, the remaining services are
      skipped: the setting is account-wide, so more calls would only add cost.
"""

from __future__ import annotations

import time
from datetime import datetime
from functools import partial
from typing import Any, ClassVar

from workload_graph.collectors.base import CallRecorder, ClientFactory, CollectionScope, SleepFn
from workload_graph.collectors.cost_common import LabelledRun, cost_snapshot
from workload_graph.cost.labels import (
    RESOURCE_COSTS_PREFIX,
    SPEND_BY_SERVICE,
    TAG_KEYS_SEEN,
    TAG_SPEND_PREFIX,
    WINDOW_TOTAL,
    payload_key,
)
from workload_graph.cost.models import AccessPlan, CostScope
from workload_graph.cost.periods import Period, complete_months, trailing_window
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.models import RawSnapshot, SourceName
from workload_graph.normalisers.cost_explorer import top_services

CE_REGION = "us-east-1"
GCU = "GetCostAndUsage"
GCUR = "GetCostAndUsageWithResources"


def _time(period: Period) -> dict[str, str]:
    return {"Start": period.start.isoformat(), "End": period.end.isoformat()}


def _dimension(key: str) -> dict[str, str]:
    return {"Type": "DIMENSION", "Key": key}


class CostExplorerCollector:
    """Collects spend by service, window totals, tag spend and resource-level spend."""

    source: ClassVar[SourceName] = "cost_explorer"
    allowed_operations: ClassVar[frozenset[str]] = frozenset({GCU, GCUR, "GetTags"})

    def __init__(self, sleep: SleepFn = time.sleep) -> None:
        self._sleep = sleep

    def collect(self, client_factory: ClientFactory, plan: AccessPlan, scope: CostScope,
                ruleset: FindingsRuleset, collected_at: datetime) -> RawSnapshot:
        """Run every labelled call within ``scope.max_calls``. Never raises on denial."""
        recorder = CallRecorder(CollectionScope(now=collected_at, max_calls=scope.max_calls),
                                self._sleep, self.allowed_operations, ruleset.not_enabled)
        run = LabelledRun(recorder)
        client = client_factory("ce", plan.access_account_id, CE_REGION)
        linked: dict[str, Any] = {"Dimensions": {"Key": "LINKED_ACCOUNT",
                                                 "Values": list(plan.linked_account_ids)}}
        months = complete_months(scope.as_of, scope.lookback_months)
        history = Period(start=months[0].start, end=scope.as_of)
        window = trailing_window(scope.as_of, scope.resource_level_window_days)

        def paged(operation: str, label: str, params: dict[str, Any]) -> None:
            for page in recorder.paginate(client, operation, params, "NextPageToken",
                                          "NextPageToken"):
                run.record(operation, label, page)

        run.run(GCU, SPEND_BY_SERVICE, lambda: paged(GCU, SPEND_BY_SERVICE, {
            "TimePeriod": _time(history), "Granularity": "MONTHLY", "Metrics": [scope.metric],
            "GroupBy": [_dimension("SERVICE"), _dimension("LINKED_ACCOUNT")], "Filter": linked}))
        run.run(GCU, WINDOW_TOTAL, lambda: paged(GCU, WINDOW_TOTAL, {
            "TimePeriod": _time(window), "Granularity": "DAILY", "Metrics": [scope.metric],
            "GroupBy": [_dimension("LINKED_ACCOUNT")], "Filter": linked}))
        run.run("GetTags", TAG_KEYS_SEEN, lambda: paged("GetTags", TAG_KEYS_SEEN, {
            "TimePeriod": _time(history), "Filter": linked}))

        seen = {t.lower(): t for page in run.pages("GetTags", TAG_KEYS_SEEN)
                for t in page.get("Tags") or [] if isinstance(t, str)}
        for wanted in sorted({k.lower() for k in scope.tag_keys}):
            spelled = seen.get(wanted)
            if spelled is None:
                continue
            label = TAG_SPEND_PREFIX + spelled
            run.run(GCU, label, partial(paged, GCU, label, {
                "TimePeriod": _time(history), "Granularity": "MONTHLY",
                "Metrics": [scope.metric], "GroupBy": [{"Type": "TAG", "Key": spelled},
                                                       _dimension("LINKED_ACCOUNT")],
                "Filter": linked}))

        if run.status_of(SPEND_BY_SERVICE) != "ok":
            recorder.notes.append(f"{payload_key(GCUR, RESOURCE_COSTS_PREFIX + '*')}: skipped "
                                  "(spend by service is unavailable)")
        else:
            services = top_services(run.pages(GCU, SPEND_BY_SERVICE), months[-1],
                                    scope.resource_level_top_services, scope.metric)
            for service in services:
                label = RESOURCE_COSTS_PREFIX + service
                if "not_enabled" in (e["status"] for e in run.labels
                                     if str(e["label"]).startswith(RESOURCE_COSTS_PREFIX)):
                    run.skip(GCUR, label, "resource-level data is not enabled")
                    continue
                run.run(GCUR, label, partial(paged, GCUR, label, {
                    "TimePeriod": _time(window), "Granularity": "DAILY",
                    "Metrics": [scope.metric],
                    "GroupBy": [_dimension("RESOURCE_ID"), _dimension("LINKED_ACCOUNT")],
                    "Filter": {"And": [linked, {"Dimensions": {"Key": "SERVICE",
                                                               "Values": [service]}}]}}))

        return cost_snapshot(self.source, plan.access_account_id, collected_at, run,
                             f"cost_explorer-{plan.access_account_id}-{scope.as_of.isoformat()}")
