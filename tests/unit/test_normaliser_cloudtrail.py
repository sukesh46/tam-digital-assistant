"""Step 4: CloudTrail normaliser tests."""

from __future__ import annotations

from typing import Any

from wg_test_helpers import ACCT_A, ACCT_C, REGION, ct_event, make_snapshot

from workload_graph.ids import ResourceKey
from workload_graph.models import EdgeType, RawSnapshot
from workload_graph.normalisers.base import NormalisedBatch
from workload_graph.normalisers.cloudtrail import normalise_cloudtrail
from workload_graph.ruleset import Ruleset

PRINCIPAL = "arn:aws:sts::111111111111:assumed-role/deployer/ci"
INST = "AWS::EC2::Instance"


def _inst(rid: str) -> ResourceKey:
    return ResourceKey(account_id=ACCT_A, region=REGION,
                       arn=f"arn:aws:ec2:{REGION}:{ACCT_A}:instance/{rid}")


def _events() -> list[dict[str, Any]]:
    return [
        ct_event("e1", "RunInstances", "2026-01-10T10:00:00Z", [(INST, "i-0seed1")]),
        ct_event("e2", "RunInstances", "2026-01-10T10:10:00Z", [(INST, "i-0seed2")]),
        ct_event("e3", "RunInstances", "2026-01-10T10:20:00Z", [(INST, "i-0new")]),
        ct_event("e4", "RunInstances", "2026-01-10T12:00:00Z", [(INST, "i-0late")]),
        ct_event("e5", "CreateBucket", "2025-09-01T10:00:00Z",
                 [("AWS::S3::Bucket", "ancient-bucket")]),
        ct_event("e6", "RunInstances", "2026-01-10T10:05:00Z", [(INST, "i-0bad")],
                 raw="{not json"),
        ct_event("e7", "RunInstances", "2026-01-10T11:00:00Z", [(INST, "i-0cross")],
                 principal="arn:aws:sts::333333333333:assumed-role/ops/x",
                 identity_account=ACCT_C),
    ]


def _snapshot(events: list[dict[str, Any]] | None = None) -> RawSnapshot:
    evs = _events() if events is None else events
    return make_snapshot("cloudtrail", {
        "_seeds": [{"ResourceIds": ["i-0seed1", "i-0seed2"]}],
        "LookupEvents": [{"Events": evs[:4]}, {"Events": evs[4:] + evs[:1]}],
    })


def _batch(events: list[dict[str, Any]] | None = None) -> NormalisedBatch:
    return normalise_cloudtrail(_snapshot(events), Ruleset())


def _created_pairs(batch: NormalisedBatch) -> set[frozenset[ResourceKey]]:
    out: set[frozenset[ResourceKey]] = set()
    for e in batch.edges:
        if e.edge_type is EdgeType.CREATED_BY_SAME_PRINCIPAL:
            assert isinstance(e.dst, ResourceKey)
            out.add(frozenset({e.src, e.dst}))
    return out


def test_events_for_seed_resources_produce_a_creator_record() -> None:
    resources = {r.key: r for r in _batch().resources}
    seed = resources[_inst("i-0seed1")]
    assert seed.attributes["created_by"] == PRINCIPAL
    assert seed.attributes["created_at"] == "2026-01-10T10:00:00+00:00"
    assert "undeclared" not in seed.attributes


def test_same_principal_within_window_produces_created_by_edges() -> None:
    pairs = _created_pairs(_batch())
    assert pairs == {
        frozenset({_inst("i-0seed1"), _inst("i-0seed2")}),
        frozenset({_inst("i-0seed1"), _inst("i-0new")}),
        frozenset({_inst("i-0seed2"), _inst("i-0new")}),
    }


def test_creation_exactly_at_window_edge_is_linked() -> None:
    events = [
        ct_event("a", "RunInstances", "2026-01-10T10:00:00Z", [(INST, "i-0seed1")]),
        ct_event("b", "RunInstances", "2026-01-10T10:30:00Z", [(INST, "i-0seed2")]),
    ]
    assert len(_created_pairs(_batch(events))) == 1


def test_creation_event_for_non_seed_resource_produces_flagged_undeclared_resource() -> None:
    resources = {r.key: r for r in _batch().resources}
    assert resources[_inst("i-0new")].attributes["undeclared"] == "true"
    assert resources[_inst("i-0new")].sources == frozenset({"cloudtrail"})


def test_events_outside_lookback_window_are_ignored() -> None:
    batch = _batch()
    assert all("ancient-bucket" not in r.key.arn for r in batch.resources)


def test_identity_account_different_from_recipient_produces_cross_account_ref() -> None:
    cross = [e for e in _batch().edges if e.edge_type is EdgeType.CROSS_ACCOUNT_REF]
    assert len(cross) == 1
    assert cross[0].src == _inst("i-0cross")
    assert isinstance(cross[0].dst, ResourceKey)
    assert cross[0].dst.account_id == ACCT_C


def test_malformed_events_are_skipped_with_a_note() -> None:
    batch = _batch()
    assert all("i-0bad" not in r.key.arn for r in batch.resources)
    assert any("malformed" in n and "e6" in n for n in batch.notes)


def test_no_raw_event_payload_is_copied_into_evidence_detail() -> None:
    for edge in _batch().edges:
        for ev in edge.evidence:
            assert "192.0.2.10" not in ev.detail
            assert "{" not in ev.detail
            assert "userAgent" not in ev.detail


def test_duplicate_events_across_pages_are_deduplicated() -> None:
    batch = _batch()
    seed_edges = [e for e in batch.edges if e.src == _inst("i-0seed1")
                  and e.edge_type is EdgeType.CREATED_BY_SAME_PRINCIPAL]
    for edge in seed_edges:
        assert len(edge.evidence) == len(set(edge.evidence))


def test_events_without_principal_are_skipped_with_note() -> None:
    event = ct_event("np", "RunInstances", "2026-01-10T10:00:00Z", [(INST, "i-0seed1")],
                     raw='{"eventName": "RunInstances", "userIdentity": {"type": "AWSService"}}')
    batch = _batch([event])
    assert batch.edges == ()
    assert any("np" in n for n in batch.notes)


def test_non_creation_events_produce_no_creator_record() -> None:
    event = ct_event("s", "StopInstances", "2026-01-10T10:00:00Z", [(INST, "i-0seed1")])
    batch = _batch([event])
    assert all("created_by" not in r.attributes for r in batch.resources)


def test_event_principal_and_event_time_tolerate_bad_values() -> None:
    from datetime import UTC, datetime

    from workload_graph.normalisers.cloudtrail import event_principal, parse_event_time

    assert event_principal(None) is None
    assert event_principal({"CloudTrailEvent": "not-json"}) is None
    assert event_principal({"CloudTrailEvent": "[]"}) is None
    assert event_principal({"CloudTrailEvent": '{"userIdentity": {"type": "Root"}}'}) is None
    assert event_principal({"CloudTrailEvent": '{"userIdentity": {"arn": ""}}'}) is None
    found = event_principal(
        {"CloudTrailEvent": '{"userIdentity": {"arn": "arn:aws:iam::111111111111:user/a"}}'}
    )
    assert found == "arn:aws:iam::111111111111:user/a"
    assert parse_event_time(None) is None
    assert parse_event_time("not-a-time") is None
    naive = parse_event_time(datetime(2026, 1, 10, 10, 0, 0))
    assert naive is not None and naive.tzinfo is UTC
    aware = parse_event_time("2026-01-10T10:00:00+00:00")
    assert aware == datetime(2026, 1, 10, 10, 0, tzinfo=UTC)


def test_arn_resource_names_are_used_as_is() -> None:
    arn = f"arn:aws:ec2:{REGION}:{ACCT_A}:instance/i-0arn"
    event = ct_event("arn1", "RunInstances", "2026-01-10T10:00:00Z", [(INST, arn)])
    batch = _batch([event])
    assert any(r.key.arn == arn for r in batch.resources)


def test_unlisted_creation_event_with_unknown_type_is_recorded_with_a_note() -> None:
    ruleset = Ruleset(creation_event_names=["CreateWidget"])
    event = ct_event("unk", "CreateWidget", "2026-01-10T10:00:00Z",
                     [("AWS::Example::Widget", "widget-1")])
    snapshot = make_snapshot("cloudtrail", {
        "_seeds": [{"ResourceIds": []}],
        "LookupEvents": [{"Events": [event]}],
    })
    batch = normalise_cloudtrail(snapshot, ruleset)
    assert any(r.key.arn.startswith("unresolved:AWS::Example::Widget:") for r in batch.resources)
    assert any("no ARN rule" in n for n in batch.notes)


def test_non_created_resource_types_on_a_creation_event_are_not_recorded_as_created() -> None:
    event = ct_event("mix", "RunInstances", "2026-01-10T10:00:00Z",
                     [(INST, "i-0mix"), ("AWS::EC2::Volume", "vol-0mix")])
    batch = _batch([event])
    assert any(r.key.arn.endswith("i-0mix") for r in batch.resources)
    assert all("vol-0mix" not in r.key.arn for r in batch.resources)


def test_later_creation_event_does_not_replace_an_earlier_one() -> None:
    early = ct_event("early", "RunInstances", "2026-01-10T10:00:00Z", [(INST, "i-0seed1")])
    late = ct_event("late", "RunInstances", "2026-01-10T10:05:00Z", [(INST, "i-0seed1")])
    batch = _batch([early, late])
    resource = next(r for r in batch.resources if r.key.arn.endswith("i-0seed1"))
    assert resource.attributes["created_at"].startswith("2026-01-10T10:00:00")


def test_cross_account_event_with_no_resource_is_noted() -> None:
    event = ct_event("bare", "RunInstances", "2026-01-10T10:00:00Z", [],
                     principal="arn:aws:sts::333333333333:assumed-role/ops/x",
                     identity_account=ACCT_C)
    batch = _batch([event])
    assert any("names no resource" in n for n in batch.notes)


def test_malformed_resource_entries_and_identity_are_skipped() -> None:
    event = ct_event("badres", "RunInstances", "2026-01-10T10:00:00Z", [(INST, "i-0ok")])
    event["Resources"] = [{"ResourceType": 1, "ResourceName": "nope"}, INST]
    event["CloudTrailEvent"] = (
        '{"userIdentity": "not-a-dict", "recipientAccountId": "111111111111"}'
    )
    batch = _batch([event])
    assert all("i-0ok" not in r.key.arn for r in batch.resources)
    assert any("no principal" in n for n in batch.notes)
