"""Step 10.3-10.6: attachment, residuals, confidence and name hints."""

from __future__ import annotations

import pytest
from wg_test_helpers import gedge, gk, gres

from workload_graph.graph.attach import affinity, attach
from workload_graph.graph.confidence import derive_confidence
from workload_graph.graph.naming import name_hint
from workload_graph.graph.residual import build_residuals
from workload_graph.graph.seeds import build_seeds
from workload_graph.models import Edge, EdgeType, Resource, Unplaced
from workload_graph.ruleset import Ruleset
from workload_graph.store.memory import InMemoryGraphStore

SM = EdgeType.STACK_MEMBER
S1, S2 = gk("s1", kind="stack"), gk("s2", kind="stack")
SUBNET, SG, VPC = gk("sub-1", kind="subnet"), gk("sg-1", kind="security-group"), gk(
    "vpc-1", kind="vpc")
SUBNET_B, SG_B = gk("sub-2", kind="subnet"), gk("sg-2", kind="security-group")


def _store(resources: list[Resource], edges: list[Edge]) -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    for r in resources:
        store.add_resource(r)
    for e in edges:
        store.add_edge(e)
    return store


def _base(extra_resources: list[Resource], extra_edges: list[Edge]) -> InMemoryGraphStore:
    """Stack s1 = {a, b}, stack s2 = {c, d}; a, b in SUBNET/SG; c, d in SUBNET_B/SG_B."""
    resources = [gres(gk(n)) for n in "abcd"] + [gres(S1, stack_name="s1"),
                                                gres(S2, stack_name="s2")]
    edges = [gedge(gk("a"), S1, SM), gedge(gk("b"), S1, SM), gedge(gk("c"), S2, SM),
             gedge(gk("d"), S2, SM)]
    for n in "ab":
        edges += [gedge(gk(n), SUBNET, EdgeType.SAME_SUBNET),
                  gedge(gk(n), SG, EdgeType.SHARED_SECURITY_GROUP)]
    for n in "cd":
        edges += [gedge(gk(n), SUBNET_B, EdgeType.SAME_SUBNET),
                  gedge(gk(n), SG_B, EdgeType.SHARED_SECURITY_GROUP)]
    return _store(resources + extra_resources, edges + extra_edges)


def _run(store: InMemoryGraphStore, ruleset: Ruleset | None = None):  # type: ignore[no-untyped-def]
    seeds = build_seeds(store)
    ids = {s.seed_id: {k.arn.rsplit("/", 1)[1] for k in s.members} for s in seeds}
    attached, rejected = attach(store, seeds, set(), set(), ruleset or Ruleset())
    return ids, attached, rejected


def _seed_named(ids: dict[str, set[str]], member: str) -> str:
    return next(sid for sid, names in ids.items() if member in names)


def test_affinity_matches_hand_computed_value() -> None:
    x = gk("x")
    store = _base([gres(x)], [gedge(x, SUBNET, EdgeType.SAME_SUBNET),
                              gedge(x, SG, EdgeType.SHARED_SECURITY_GROUP),
                              gedge(gk("a"), x, EdgeType.CREATED_BY_SAME_PRINCIPAL)])
    ids, attached, rejected = _run(store)
    assert rejected == []
    assert len(attached) == 1
    assert attached[0].key == x
    assert attached[0].seed_id == _seed_named(ids, "a")
    assert attached[0].affinity == round(1 - 0.6 * 0.6 * 0.7, 6)
    assert attached[0].reasons == (EdgeType.CREATED_BY_SAME_PRINCIPAL,
                                   EdgeType.SAME_SUBNET, EdgeType.SHARED_SECURITY_GROUP)
    assert attached[0].evidence


def test_affinity_is_bounded_and_order_independent() -> None:
    assert affinity([0.4, 0.4, 0.3]) == affinity([0.3, 0.4, 0.4]) == 0.748
    assert affinity([]) == 0.0
    assert affinity([1.0, 0.5]) == 1.0


def test_one_weak_edge_alone_does_not_attach() -> None:
    x = gk("x")
    store = _base([gres(x)], [gedge(x, SUBNET, EdgeType.SAME_SUBNET)])
    _, attached, rejected = _run(store)
    assert attached == []
    rejection = next(r for r in rejected if r.key == x)
    assert rejection.reason == "below_threshold"
    assert rejection.best_affinity == 0.4


def test_many_members_linked_by_the_same_edge_type_count_once() -> None:
    x = gk("x")
    edges = [gedge(gk("a"), x, EdgeType.CREATED_BY_SAME_PRINCIPAL),
             gedge(gk("b"), x, EdgeType.CREATED_BY_SAME_PRINCIPAL),
             gedge(x, SUBNET, EdgeType.SAME_SUBNET)]
    _, _, rejected = _run(_base([gres(x)], edges))
    assert next(r for r in rejected if r.key == x).best_affinity == round(1 - 0.7 * 0.6, 6)


def _equally_linked() -> InMemoryGraphStore:
    x = gk("x")
    edges = [gedge(x, SUBNET, EdgeType.SAME_SUBNET), gedge(x, SG, EdgeType.SHARED_SECURITY_GROUP),
             gedge(x, SUBNET_B, EdgeType.SAME_SUBNET),
             gedge(x, SG_B, EdgeType.SHARED_SECURITY_GROUP)]
    return _base([gres(x)], edges)


def test_ambiguous_resources_are_not_attached_and_are_reported() -> None:
    ids, attached, rejected = _run(_equally_linked())
    assert [a.key for a in attached] == []
    rejection = next(r for r in rejected if r.key == gk("x"))
    assert rejection.reason == "ambiguous"
    assert rejection.candidates == tuple(sorted(ids))


def test_tie_breaking_is_deterministic_lowest_workload_id() -> None:
    ids, attached, _ = _run(_equally_linked(), Ruleset(attach_margin=0.0))
    assert [a.seed_id for a in attached] == [min(ids)]


def test_resource_without_links_to_any_cluster_is_no_edges() -> None:
    _, _, rejected = _run(_base([gres(gk("lonely"))], []))
    assert next(r for r in rejected if r.key == gk("lonely")).reason == "no_edges"


def test_shared_and_excluded_resources_are_not_candidates() -> None:
    store = _base([gres(gk("x")), gres(gk("y"))], [])
    attached, rejected = attach(store, build_seeds(store), {gk("x")}, {gk("y").key_str()},
                                Ruleset())
    keys = {a.key for a in attached} | {r.key for r in rejected}
    assert gk("x") not in keys and gk("y") not in keys


def test_zero_weight_cross_account_edges_do_not_link() -> None:
    x = gk("x")
    store = _base([gres(x)], [gedge(gk("a"), x, EdgeType.CROSS_ACCOUNT_REF)])
    _, _, rejected = _run(store)
    assert next(r for r in rejected if r.key == x).reason == "no_edges"


# --- residual -------------------------------------------------------------------------------

def test_residual_groups_are_formed_per_vpc() -> None:
    r1, r2, r3 = gres(gk("r1"), vpc_id="vpc-1"), gres(gk("r2"), vpc_id="vpc-1"), gres(
        gk("r3"), vpc_id="vpc-2")
    store = _store([r1, r2, r3], [])
    unplaced = [Unplaced(key=r.key, reason="no_edges") for r in (r3, r2, r1)]
    groups, unmapped = build_residuals(unplaced, store, set())
    assert [(g.vpc_id, [m.key for m in g.members]) for g in groups] == [
        ("vpc-1", [gk("r1"), gk("r2")]), ("vpc-2", [gk("r3")])]
    assert all(g.confidence == "low" for g in groups)
    assert unmapped == []


def test_isolated_resource_is_unmapped() -> None:
    store = _store([gres(gk("iso"))], [])
    groups, unmapped = build_residuals([Unplaced(key=gk("iso"), reason="no_edges")], store,
                                       set())
    assert groups == [] and [u.key for u in unmapped] == [gk("iso")]


def test_shared_infrastructure_is_never_in_residual_groups() -> None:
    store = _store([gres(VPC, vpc_id="vpc-1")], [])
    groups, unmapped = build_residuals([Unplaced(key=VPC, reason="no_edges")], store, {VPC})
    assert groups == [] and unmapped == []


# --- confidence -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("declared", "total", "expected"),
    [(4, 5, "high"), (8, 10, "high"), (79, 100, "medium"), (1, 2, "medium"),
     (49, 100, "low"), (0, 3, "low"), (5, 5, "high")],
)
def test_confidence_boundaries(declared: int, total: int, expected: str) -> None:
    assert derive_confidence(declared, total, ["stacks", "topology"], Ruleset()) == expected


def test_single_source_workload_is_capped_at_medium() -> None:
    assert derive_confidence(5, 5, ["stacks"], Ruleset()) == "medium"
    assert derive_confidence(1, 5, ["stacks"], Ruleset()) == "low"


def test_confidence_of_empty_workload_is_low() -> None:
    assert derive_confidence(0, 0, ["stacks", "tags"], Ruleset()) == "low"


# --- name hint ------------------------------------------------------------------------------

def test_name_hint_prefers_most_common_workload_tag_value() -> None:
    members = [gres(gk("a"), tags='{"App": "orders"}', stack_name="s1"),
               gres(gk("b"), tags='{"app": "orders"}'),
               gres(gk("c"), tags='{"project": "billing"}')]
    assert name_hint(members, Ruleset(), "abc") == "orders"


def test_name_hint_falls_back_to_most_common_stack_name() -> None:
    members = [gres(gk("a"), stack_name="web"), gres(gk("b"), stack_name="web"),
               gres(gk("c"), stack_name="db")]
    assert name_hint(members, Ruleset(), "abc") == "web"


def test_name_hint_falls_back_to_workload_id() -> None:
    assert name_hint([gres(gk("a"))], Ruleset(), "0123456789ab") == "workload-0123456789ab"


def test_name_hint_ties_break_alphabetically() -> None:
    members = [gres(gk("a"), tags='{"app": "zeta"}'), gres(gk("b"), tags='{"app": "alpha"}')]
    assert name_hint(members, Ruleset(), "x") == "alpha"
    stacks = [gres(gk("a"), stack_name="zz"), gres(gk("b"), stack_name="aa")]
    assert name_hint(stacks, Ruleset(), "x") == "aa"
