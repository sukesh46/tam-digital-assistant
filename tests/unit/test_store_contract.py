"""Step 8 (and 14): GraphStore contract tests, run against every implementation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from workload_graph.ids import ResourceKey
from workload_graph.models import Edge, EdgeType, Evidence, ExternalRef, Resource
from workload_graph.store.base import GraphStore
from workload_graph.store.memory import InMemoryGraphStore

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _key(name: str, account: str = "111111111111") -> ResourceKey:
    return ResourceKey(account_id=account, region="eu-west-1", arn=f"arn:aws:ec2:x:{name}")


def _ev(ref: str) -> Evidence:
    return Evidence(source="topology", observed_at=T0, raw_ref=f"s#{ref}", detail="d")


def _res(name: str, account: str = "111111111111", **kw: object) -> Resource:
    return Resource.model_validate({"key": _key(name, account), "resource_type": "t", **kw})


def _memory(_tmp: Path) -> GraphStore:
    return InMemoryGraphStore()


def _parquet(tmp: Path) -> GraphStore:
    pytest.importorskip("pyarrow")
    from workload_graph.store.parquet import ParquetGraphStore

    return ParquetGraphStore(tmp / "store")


FACTORIES: dict[str, Callable[[Path], GraphStore]] = {"memory": _memory, "parquet": _parquet}


@pytest.fixture(params=sorted(FACTORIES))
def store(request: pytest.FixtureRequest, tmp_path: Path) -> GraphStore:
    return FACTORIES[request.param](tmp_path)


def test_store_satisfies_protocol(store: GraphStore) -> None:
    assert isinstance(store, GraphStore)


def test_adding_same_resource_twice_is_idempotent_and_merges(store: GraphStore) -> None:
    store.add_resource(_res("a", sources=frozenset({"tags"}), tags={"app": "x"}))
    store.add_resource(_res("a", sources=frozenset({"topology"}), tags={"env": "prod"}))
    store.add_resource(_res("a", sources=frozenset({"topology"}), tags={"env": "prod"}))
    resources = store.resources()
    assert len(resources) == 1
    assert resources[0].sources == frozenset({"tags", "topology"})
    assert resources[0].tags == {"app": "x", "env": "prod"}


def test_adding_same_edge_twice_merges_evidence_without_duplicates(store: GraphStore) -> None:
    a, b = _key("a"), _key("b")
    store.add_edge(Edge(src=a, dst=b, edge_type=EdgeType.SAME_VPC, evidence=(_ev("1"),)))
    store.add_edge(Edge(src=a, dst=b, edge_type=EdgeType.SAME_VPC, evidence=(_ev("2"), _ev("1"))))
    edges = store.edges()
    assert len(edges) == 1
    assert [e.raw_ref for e in edges[0].evidence] == ["s#1", "s#2"]


def test_neighbour_queries_return_sorted_results(store: GraphStore) -> None:
    hub = _key("m")
    for name in ("z", "b", "q"):
        store.add_edge(Edge(src=_key(name), dst=hub, edge_type=EdgeType.SAME_SUBNET,
                            evidence=(_ev(name),)))
    store.add_edge(Edge(src=hub, dst=_key("a"), edge_type=EdgeType.LB_TARGET,
                        evidence=(_ev("a"),)))
    store.add_edge(Edge(src=hub, dst=ExternalRef(account_id="333333333333",
                                                 reason="out_of_scope"),
                        edge_type=EdgeType.CROSS_ACCOUNT_REF, evidence=(_ev("x"),)))
    assert store.neighbours(hub) == [_key("a"), _key("b"), _key("q"), _key("z")]
    incident = store.edges_of(hub)
    assert [e.identity() for e in incident] == sorted(e.identity() for e in incident)
    assert len(incident) == 5


def test_resources_from_several_accounts_coexist(store: GraphStore) -> None:
    store.add_resource(_res("a", "222222222222"))
    store.add_resource(_res("a", "111111111111"))
    assert store.accounts() == ["111111111111", "222222222222"]
    assert len(store.resources()) == 2


def test_export_is_in_stable_sorted_order(store: GraphStore) -> None:
    for name in ("c", "a", "b"):
        store.add_resource(_res(name))
        store.add_edge(Edge(src=_key(name), dst=_key("hub"), edge_type=EdgeType.SAME_VPC,
                            evidence=(_ev(name),)))
    assert [r.key for r in store.resources()] == sorted(r.key for r in store.resources())
    assert [e.identity() for e in store.edges()] == sorted(e.identity() for e in store.edges())


def test_store_is_append_only(store: GraphStore) -> None:
    for name in ("remove_resource", "remove_edge", "delete", "clear", "discard"):
        assert not hasattr(store, name)


def test_get_and_has_resource(store: GraphStore) -> None:
    store.add_resource(_res("a"))
    assert store.has_resource(_key("a"))
    assert not store.has_resource(_key("b"))
    resource = store.get_resource(_key("a"))
    assert resource is not None and resource.key == _key("a")
    assert store.get_resource(_key("b")) is None


def test_edges_to_unknown_resources_are_kept(store: GraphStore) -> None:
    store.add_edge(Edge(src=_key("a"), dst=_key("ghost"), edge_type=EdgeType.SAME_VPC,
                        evidence=(_ev("1"),)))
    assert len(store.edges()) == 1
    assert store.resources() == []
    assert store.neighbours(_key("a")) == [_key("ghost")]


def test_self_edges_are_not_neighbours(store: GraphStore) -> None:
    a = _key("a")
    store.add_edge(Edge(src=a, dst=a, edge_type=EdgeType.TAG_WORKLOAD_KEY, evidence=(_ev("1"),)))
    assert store.neighbours(a) == []
    assert len(store.edges_of(a)) == 1


def test_parquet_store_round_trips_through_a_new_instance(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from workload_graph.store.parquet import ParquetGraphStore

    path = tmp_path / "store"
    first = ParquetGraphStore(path)
    first.add_resource(_res("a", sources=frozenset({"tags"}), tags={"app": "orders"}))
    first.add_edge(Edge(src=_key("a"), dst=_key("b"), edge_type=EdgeType.SAME_VPC,
                        evidence=(_ev("1"),)))
    first.add_edge(Edge(src=_key("a"), dst=ExternalRef(account_id="333333333333",
                                                       reason="out_of_scope"),
                        edge_type=EdgeType.CROSS_ACCOUNT_REF, evidence=(_ev("x"),)))
    second = ParquetGraphStore(path)
    assert [r.key for r in second.resources()] == [r.key for r in first.resources()]
    assert second.resources()[0].tags == {"app": "orders"}
    assert [e.identity() for e in second.edges()] == [e.identity() for e in first.edges()]
    assert second.neighbours(_key("a")) == first.neighbours(_key("a"))


def test_add_batch_adds_resources_and_edges(store: GraphStore) -> None:
    from workload_graph.normalisers.base import NormalisedBatch

    batch = NormalisedBatch(
        resources=(_res("a"), _res("b")),
        edges=(Edge(src=_key("a"), dst=_key("b"), edge_type=EdgeType.SAME_VPC,
                    evidence=(_ev("1"),)),),
    )
    store.add_batch(batch)
    assert len(store.resources()) == 2
    assert len(store.edges()) == 1
