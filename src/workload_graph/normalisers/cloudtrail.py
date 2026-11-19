"""CloudTrail normaliser (seeded ``LookupEvents``).

Status: real
Purpose: turn seeded management events into creator records, ``CREATED_BY_SAME_PRINCIPAL``
    edges, undeclared resources and cross-account references.
Inputs: ``RawSnapshot`` with ``LookupEvents`` pages and the collector metadata key
    ``_seeds`` (``[{"ResourceIds": [...]}]``) naming the seed resources; ``Ruleset``.
Outputs: ``NormalisedBatch``.
Planned dependencies: none.
Notes:
    - The lookback window is measured from ``snapshot.collected_at``, never the wall clock.
    - Malformed events are skipped with a note, never an exception.
    - Evidence ``detail`` holds only the event name and time; the raw event payload is never
      copied anywhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from workload_graph.ids import ResourceKey, arn_for, key_for_arn
from workload_graph.models import Edge, EdgeType, Evidence, RawSnapshot, Resource
from workload_graph.normalisers.base import BatchBuilder, NormalisedBatch, evidence
from workload_graph.ruleset import Ruleset

_ACCOUNT_RE = re.compile(r"[0-9]{12}")

# ASSUMPTION: these are the resource types each creation event creates; others in the event's
# Resources list are only referenced. Unlisted creation events treat every resource as created.
CREATED_TYPES: dict[str, frozenset[str]] = {
    "RunInstances": frozenset({"AWS::EC2::Instance"}),
    "CreateBucket": frozenset({"AWS::S3::Bucket"}),
    "CreateFunction": frozenset({"AWS::Lambda::Function"}),
    "CreateFunction20150331": frozenset({"AWS::Lambda::Function"}),
    "CreateLoadBalancer": frozenset({"AWS::ElasticLoadBalancingV2::LoadBalancer"}),
    "CreateDBInstance": frozenset({"AWS::RDS::DBInstance"}),
    "CreateTable": frozenset({"AWS::DynamoDB::Table"}),
}


@dataclass(frozen=True)
class _Creation:
    principal: str
    at: datetime
    key: ResourceKey
    evidence: Evidence


def event_principal(event: Any) -> str | None:
    """Principal ARN of a ``LookupEvents`` event, or None if absent or malformed.

    Any: raw boto3 event dict.
    """
    if not isinstance(event, dict):
        return None
    try:
        body = json.loads(event.get("CloudTrailEvent") or "")
    except (TypeError, ValueError):
        return None
    identity = body.get("userIdentity") if isinstance(body, dict) else None
    arn = identity.get("arn") if isinstance(identity, dict) else None
    return arn if isinstance(arn, str) and arn else None


def parse_event_time(value: Any) -> datetime | None:
    """Parse an event time (datetime or ISO string) to aware UTC. Any: raw boto3 value."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        # ASSUMPTION: naive event times from the API are UTC.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _seed_ids(snapshot: RawSnapshot) -> set[str]:
    seeds: set[str] = set()
    for page in snapshot.payload.get("_seeds", []):
        seeds.update(s for s in page.get("ResourceIds") or [] if isinstance(s, str))
    return seeds


def normalise_cloudtrail(snapshot: RawSnapshot, ruleset: Ruleset) -> NormalisedBatch:
    """Normalise a seeded CloudTrail snapshot."""
    builder = BatchBuilder()
    seeds = _seed_ids(snapshot)
    cutoff = snapshot.collected_at - timedelta(days=ruleset.cloudtrail_lookback_days)
    creation_names = set(ruleset.creation_event_names)

    events: dict[str, tuple[str, dict[str, Any]]] = {}
    for page_index, page in enumerate(snapshot.payload.get("LookupEvents", [])):
        for item_index, item in enumerate(page.get("Events") or []):
            event_id = item.get("EventId") if isinstance(item, dict) else None
            if isinstance(event_id, str) and event_id not in events:
                events[event_id] = (f"/payload/LookupEvents/{page_index}/Events/{item_index}",
                                    item)

    creations: dict[str, _Creation] = {}
    too_old = 0
    for event_id in sorted(events):
        pointer, item = events[event_id]
        try:
            body = json.loads(item.get("CloudTrailEvent") or "")
        except (TypeError, ValueError):
            body = None
        at = parse_event_time(item.get("EventTime"))
        if not isinstance(body, dict) or at is None:
            builder.note(f"cloudtrail: skipped malformed event {event_id}")
            continue
        raw_identity = body.get("userIdentity")
        identity: dict[str, Any] = raw_identity if isinstance(raw_identity, dict) else {}
        principal = identity.get("arn")
        if not isinstance(principal, str) or not principal:
            builder.note(f"cloudtrail: event {event_id} has no principal ARN; skipped")
            continue
        if at < cutoff:
            too_old += 1
            continue
        name = str(item.get("EventName", ""))
        ev = evidence(snapshot, pointer, f"{name} at {at.isoformat()}", observed_at=at)
        touched: list[tuple[ResourceKey, str, str]] = []
        for res in item.get("Resources") or []:
            rtype, rname = res.get("ResourceType"), res.get("ResourceName")
            if not isinstance(rtype, str) or not isinstance(rname, str):
                continue
            if rname.startswith("arn:"):
                arn = rname
            else:
                resolution = arn_for(rtype, rname, snapshot.account_id, snapshot.region)
                arn = resolution.arn
                if resolution.note:
                    builder.note(f"cloudtrail: {resolution.note}")
            touched.append((key_for_arn(arn, snapshot.account_id, snapshot.region),
                            rtype, rname))

        if name in creation_names:
            created_types = CREATED_TYPES.get(name)
            for key, rtype, rname in touched:
                if created_types is not None and rtype not in created_types:
                    continue
                previous = creations.get(key.key_str())
                if previous is None or at < previous.at:
                    creations[key.key_str()] = _Creation(principal, at, key, ev)
                attributes = {"created_by": principal, "created_at": at.isoformat()}
                if rname not in seeds and key.arn not in seeds:
                    attributes["undeclared"] = "true"
                builder.add_resource(Resource(key=key, resource_type=rtype,
                                              attributes=attributes,
                                              sources=frozenset({"cloudtrail"})))

        id_account, recipient = identity.get("accountId"), body.get("recipientAccountId")
        if (isinstance(id_account, str) and isinstance(recipient, str)
                and _ACCOUNT_RE.fullmatch(id_account) and _ACCOUNT_RE.fullmatch(recipient)
                and id_account != recipient):
            if not touched:
                builder.note(f"cloudtrail: cross-account event {event_id} names no resource")
            dst = key_for_arn(principal, id_account, "global")
            for key, _, _ in touched:
                builder.add_edge(Edge(src=key, dst=dst, edge_type=EdgeType.CROSS_ACCOUNT_REF,
                                      evidence=(ev,)))

    if too_old:
        builder.note(f"cloudtrail: {too_old} events outside the "
                     f"{ruleset.cloudtrail_lookback_days}-day lookback ignored")
    _link_same_principal(builder, list(creations.values()), ruleset)
    return builder.build()


def _link_same_principal(builder: BatchBuilder, creations: list[_Creation],
                         ruleset: Ruleset) -> None:
    window = timedelta(minutes=ruleset.cloudtrail_window_minutes)
    by_principal: dict[str, list[_Creation]] = {}
    for creation in creations:
        by_principal.setdefault(creation.principal, []).append(creation)
    for principal in sorted(by_principal):
        ordered = sorted(by_principal[principal], key=lambda c: (c.at, c.key.key_str()))
        for i, first in enumerate(ordered):
            for second in ordered[i + 1:]:
                if second.at - first.at > window:
                    break
                if first.key == second.key:
                    continue
                builder.add_edge(Edge(
                    src=first.key, dst=second.key,
                    edge_type=EdgeType.CREATED_BY_SAME_PRINCIPAL,
                    evidence=tuple(sorted({first.evidence, second.evidence},
                                          key=Evidence.sort_key)),
                ))
