"""Step 4: tags normaliser tests."""

from __future__ import annotations

import networkx as nx
from wg_test_helpers import ACCT_A, REGION, load_snapshot, make_snapshot

from workload_graph.ids import ResourceKey
from workload_graph.models import EdgeType
from workload_graph.normalisers.tags import normalise_tags
from workload_graph.ruleset import Ruleset


def _key(arn: str, region: str = REGION) -> ResourceKey:
    return ResourceKey(account_id=ACCT_A, region=region, arn=arn)


I1 = _key("arn:aws:ec2:eu-west-1:111111111111:instance/i-0orders1")
I2 = _key("arn:aws:ec2:eu-west-1:111111111111:instance/i-0orders2")
DB = _key("arn:aws:rds:eu-west-1:111111111111:db:orders-db")
BUCKET = _key("arn:aws:s3:::billing-bucket", region="global")
UNTAGGED = _key("arn:aws:ec2:eu-west-1:111111111111:instance/i-0untagged")
CONFLICT = _key("arn:aws:ec2:eu-west-1:111111111111:instance/i-0conflict")


def _batch():  # type: ignore[no-untyped-def]
    return normalise_tags(load_snapshot("unit/111111111111/tags.json"), Ruleset())


def _tag_components() -> list[set[ResourceKey]]:
    graph: nx.Graph[ResourceKey] = nx.Graph()
    for edge in _batch().edges:
        assert edge.edge_type is EdgeType.TAG_WORKLOAD_KEY
        assert isinstance(edge.dst, ResourceKey)
        graph.add_edge(edge.src, edge.dst)
    return [set(c) for c in nx.connected_components(graph)]


def test_resources_sharing_a_workload_tag_value_are_linked_by_tag_edges() -> None:
    assert {I1, I2, DB} in _tag_components()


def test_lone_tagged_resource_keeps_its_declaration_as_a_self_edge() -> None:
    assert {BUCKET} in _tag_components()


def test_non_workload_tags_are_kept_on_the_resource_but_produce_no_edge() -> None:
    resources = {r.key: r for r in _batch().resources}
    assert resources[I2].tags["env"] == "prod"
    assert all("env" not in e.evidence[0].detail for e in _batch().edges)


def test_untagged_resources_are_still_emitted() -> None:
    resources = {r.key: r for r in _batch().resources}
    assert UNTAGGED in resources
    assert resources[UNTAGGED].tags == {}
    assert resources[UNTAGGED].sources == frozenset({"tags"})


def test_conflicting_workload_tag_values_produce_a_note_and_no_edges() -> None:
    batch = _batch()
    assert all(CONFLICT not in (e.src, e.dst) for e in batch.edges)
    assert any("conflict" in n and "i-0conflict" in n for n in batch.notes)
    resources = {r.key: r for r in batch.resources}
    assert resources[CONFLICT].attributes["tag_conflict"] == "true"


def test_tag_keys_are_matched_case_insensitively() -> None:
    components = _tag_components()
    assert any(I1 in c and I2 in c for c in components)


def test_empty_pages_are_tolerated() -> None:
    snap = make_snapshot("tags", {"GetResources": [{"ResourceTagMappingList": []}]})
    batch = normalise_tags(snap, Ruleset())
    assert batch.resources == ()
    assert batch.edges == ()
    assert normalise_tags(make_snapshot("tags", {}), Ruleset()).resources == ()


def test_s3_bucket_keys_use_global_region() -> None:
    resources = {r.key: r for r in _batch().resources}
    assert resources[BUCKET].resource_type == "AWS::S3::Bucket"


def test_name_comes_from_name_tag() -> None:
    resources = {r.key: r for r in _batch().resources}
    assert resources[I1].name == "orders-web-1"


def test_evidence_points_back_to_the_raw_datum() -> None:
    edge = next(e for e in _batch().edges if e.src == I2)
    ev = edge.evidence[0]
    assert ev.source == "tags"
    assert ev.raw_ref == (
        "unit-111111111111-eu-west-1-tags#/payload/GetResources/0/ResourceTagMappingList/1"
    )


def test_output_is_sorted() -> None:
    batch = _batch()
    assert [r.key for r in batch.resources] == sorted(r.key for r in batch.resources)
    assert [e.identity() for e in batch.edges] == sorted(e.identity() for e in batch.edges)


def test_duplicate_mappings_across_pages_are_merged() -> None:
    arn = "arn:aws:ec2:eu-west-1:111111111111:instance/i-dup"
    snap = make_snapshot("tags", {"GetResources": [
        {"ResourceTagMappingList": [{"ResourceARN": arn, "Tags": [{"Key": "a", "Value": "1"}]}]},
        {"ResourceTagMappingList": [{"ResourceARN": arn, "Tags": [{"Key": "b", "Value": "2"}]}]},
    ]})
    batch = normalise_tags(snap, Ruleset())
    assert len(batch.resources) == 1
    assert batch.resources[0].tags == {"a": "1", "b": "2"}
