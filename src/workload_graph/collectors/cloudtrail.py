"""Seeded CloudTrail collector (``LookupEvents``).

Status: real
Purpose: collect management events for known seed resources only, so cost scales with the
    number of seeds rather than with log volume.
Inputs: client factory, scope (seed ids, lookback, window, creation event names, budget).
Outputs: ``RawSnapshot`` with ``LookupEvents`` pages and ``_seeds`` metadata.
Planned dependencies: boto3.
Notes:
    - Pass 1: one ``LookupEvents`` query per seed id (``ResourceName`` attribute).
    - Pass 2: one query per creation event name per merged window around the creation times
      found in pass 1; only events by principals that created a seed in pass 1 are kept.
    - Both passes share the call budget.
    - # ASSUMPTION: LookupEvents accepts exactly one lookup attribute per call and is rate
      limited (about 2 requests per second per account and region); verify.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from workload_graph.collectors.base import (
    BaseCollector,
    CallRecorder,
    ClientFactory,
    CollectionScope,
)
from workload_graph.models import SourceName
from workload_graph.normalisers.cloudtrail import event_principal, parse_event_time


def merge_windows(times: list[datetime], half_width: timedelta) -> list[tuple[datetime, datetime]]:
    """Merge ``[t - w, t + w]`` intervals into sorted, non-overlapping windows."""
    merged: list[tuple[datetime, datetime]] = []
    for t in sorted(set(times)):
        start, end = t - half_width, t + half_width
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


class CloudTrailCollector(BaseCollector):
    """Collects seeded CloudTrail management events."""

    source: ClassVar[SourceName] = "cloudtrail"
    allowed_operations: ClassVar[frozenset[str]] = frozenset({"LookupEvents"})

    def _run(self, recorder: CallRecorder, client_factory: ClientFactory, account_id: str,
             region: str, scope: CollectionScope) -> None:
        client = client_factory("cloudtrail", account_id, region)
        seeds = sorted(set(scope.seed_resource_ids))
        recorder.payload["_seeds"] = [{"ResourceIds": seeds}]
        creation_names = set(scope.creation_event_names)
        start, end = scope.now - timedelta(days=scope.lookback_days), scope.now
        creators: set[str] = set()
        creation_times: list[datetime] = []

        def query(attribute: str, value: str, window_start: datetime,
                  window_end: datetime) -> Any:
            params = {
                "LookupAttributes": [{"AttributeKey": attribute, "AttributeValue": value}],
                "StartTime": window_start,
                "EndTime": window_end,
            }
            return recorder.paginate(client, "LookupEvents", params, "NextToken", "NextToken")

        for seed in seeds:
            def pass_one(seed: str = seed) -> None:
                for page in query("ResourceName", seed, start, end):
                    recorder.record("LookupEvents", page)
                    for event in page.get("Events") or []:
                        if event.get("EventName") not in creation_names:
                            continue
                        principal = event_principal(event)
                        at = parse_event_time(event.get("EventTime"))
                        if principal and at:
                            creators.add(principal)
                            creation_times.append(at)

            if not recorder.step(f"LookupEvents ResourceName={seed}", pass_one):
                return

        windows = merge_windows(creation_times, timedelta(minutes=scope.window_minutes))
        for window_start, window_end in windows:
            for name in sorted(creation_names):
                def pass_two(name: str = name, ws: datetime = window_start,
                             we: datetime = window_end) -> None:
                    for page in query("EventName", name, ws, we):
                        kept = [e for e in page.get("Events") or []
                                if event_principal(e) in creators]
                        recorder.record("LookupEvents", {**page, "Events": kept})

                if not recorder.step(f"LookupEvents EventName={name}", pass_two):
                    return
