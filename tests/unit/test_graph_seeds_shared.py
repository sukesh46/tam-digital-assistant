"""Step 10.1-10.2: seeds and shared infrastructure."""

from __future__ import annotations

import random

from wg_test_helpers import gedge, gk, gres

from workload_graph.graph.seeds import build_seeds, seed_id_for
from workload_graph.graph.shared import find_shared
from workload_graph.models import Edge, EdgeType, Resource
from workload_graph.ruleset import Ruleset
from workload_graph.store.memory import InMemoryGraphStore

SM, TAG = EdgeType.STACK_MEMBER, EdgeType.TAG_WORKLOAD_KEY


def _stack(name: str) -> object:
    return gk(name, kind="stack")


def _store(resources: list[Resource], edges: list[Edge]) -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    for r in resources:
        store.add_resource(r)
    for e in edges:
        store.add_edge(e)
    return store


def _two_stacks(extra_edges: list[Edge] | None = None,
                extra_resources: list[Resource] | None = None) -> InMemoryGraphStore:
    s1, s2 = _stack("s1"), _stack("s2")
    names = ["a", "b", "c", "d"]
    resources = [gres(gk(n)) for n in names] + [gres(s1, stack_name="s1"),
                                                gres(s2, stack_name="s2")]
    edges = [gedge(gk("a"), s1, SM), gedge(gk("b"), s1, SM),
             gedge(gk("c"), s2, SM), gedge(gk("d"), s2, SM)]
    return _store(resources + (extra_resources or []), edges + (extra_edges or []))


def _member_sets(store: InMemoryGraphStore) -> list[set[str]]:
    return [{k.arn.rsplit("/", 1)[1] for k in s.members} for s in build_seeds(store)]


def test_two_stacks_with_no_overlap_stay_separate() -> None:
    assert sorted(map(sorted, _member_sets(_two_stacks()))) == [
        ["a", "b", "s1"], ["c", "d", "s2"]]


def test_two_stacks_linked_by_one_tagged_resource_merge_and_record_it() -> None:
    store = _two_stacks([gedge(gk("c"), gk("b"), TAG, detail="workload tag app=orders")])
    seeds = build_seeds(store)
    assert len(seeds) == 1
    assert len(seeds[0].members) == 6
    assert seeds[0].declarations == ("stack:s1", "stack:s2", "tag:app=orders")
    assert seeds[0].merged


def test_tag_group_spanning_two_stacks_merges_them() -> None:
    store = _two_stacks([gedge(gk("d"), gk("a"), TAG, detail="workload tag app=x")])
    assert len(build_seeds(store)) == 1


def test_lone_untagged_resource_is_in_no_seed() -> None:
    store = _two_stacks(extra_resources=[gres(gk("lonely"))])
    assert all(gk("lonely") not in s.members for s in build_seeds(store))


def test_lone_tagged_resource_with_self_edge_forms_its_own_seed() -> None:
    store = _two_stacks([gedge(gk("t"), gk("t"), TAG, detail="workload tag app=solo")],
                        [gres(gk("t"))])
    assert any(s.members == (gk("t"),) for s in build_seeds(store))


def test_resource_with_tag_conflict_is_excluded_from_seeds() -> None:
    store = _two_stacks(extra_resources=[gres(gk("a"), tag_conflict="true")])
    seeds = build_seeds(store, excluded={gk("a").key_str()})
    assert all(gk("a") not in s.members for s in seeds)


def test_edge_endpoints_that_are_not_resources_are_not_members() -> None:
    store = _store([gres(gk("a")), gres(gk("b"))],
                   [gedge(gk("a"), _stack("ghost"), SM), gedge(gk("b"), _stack("ghost"), SM)])
    seeds = build_seeds(store)
    assert [s.members for s in seeds] == [(gk("a"), gk("b"))]


def test_seed_ids_are_stable_under_input_shuffling() -> None:
    base = _two_stacks([gedge(gk("c"), gk("b"), TAG, detail="workload tag app=o")])
    resources, edges = base.resources(), base.edges()
    expected = build_seeds(base)
    rng = random.Random(7)
    for _ in range(5):
        r, e = resources[:], edges[:]
        rng.shuffle(r)
        rng.shuffle(e)
        assert build_seeds(_store(r, e)) == expected


def test_seed_id_is_first_12_hex_of_sha256_over_sorted_member_keys() -> None:
    import hashlib

    keys = [gk("b"), gk("a")]
    text = "\n".join(sorted(k.key_str() for k in keys))
    assert seed_id_for(keys) == hashlib.sha256(text.encode()).hexdigest()[:12]


# --- shared infrastructure -----------------------------------------------------------------

LB = gk("lb", kind="loadbalancer")
VPC = gk("vpc-1", kind="vpc")


def test_load_balancer_fronting_targets_from_two_stacks_is_shared() -> None:
    store = _two_stacks([gedge(LB, gk("a"), EdgeType.LB_TARGET),
                         gedge(LB, gk("c"), EdgeType.LB_TARGET)], [gres(LB)])
    seeds = build_seeds(store)
    shared = find_shared(store, seeds, Ruleset())
    assert list(shared) == [LB]
    assert len(shared[LB]) == 2
    assert len(seeds) == 2


def test_load_balancer_fronting_one_stack_is_not_shared() -> None:
    store = _two_stacks([gedge(LB, gk("a"), EdgeType.LB_TARGET),
                         gedge(LB, gk("b"), EdgeType.LB_TARGET)], [gres(LB)])
    assert find_shared(store, build_seeds(store), Ruleset()) == {}


def test_vpc_containing_two_workloads_is_shared() -> None:
    edges = [gedge(gk(n), VPC, EdgeType.SAME_VPC) for n in "abcd"]
    store = _two_stacks(edges, [gres(VPC, resource_type="AWS::EC2::VPC", vpc_id="vpc-1")])
    shared = find_shared(store, build_seeds(store), Ruleset())
    assert VPC in shared


def test_consumer_linking_out_to_two_workloads_containers_is_not_shared() -> None:
    sub1, sub2 = gk("sub1", kind="subnet"), gk("sub2", kind="subnet")
    edges = [gedge(sub1, _stack("s1"), SM), gedge(sub2, _stack("s2"), SM),
             gedge(gk("x"), sub1, EdgeType.SAME_SUBNET),
             gedge(gk("x"), sub2, EdgeType.SAME_SUBNET)]
    store = _two_stacks(edges, [gres(sub1), gres(sub2), gres(gk("x"))])
    assert find_shared(store, build_seeds(store), Ruleset()) == {}


def test_declared_hub_is_never_marked_shared() -> None:
    edges = [gedge(LB, _stack("s1"), SM), gedge(LB, gk("a"), EdgeType.LB_TARGET),
             gedge(LB, gk("c"), EdgeType.LB_TARGET)]
    store = _two_stacks(edges, [gres(LB)])
    assert find_shared(store, build_seeds(store), Ruleset()) == {}
