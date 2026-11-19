"""F8 snapshot cache and access plan; F9 resource index and placement. Offline."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from wg_cost_builders import cost_snapshot
from wg_test_helpers import ACCT_A, ACCT_B, REGION

from workload_graph.cost.cache import SnapshotCache
from workload_graph.findings.index import ResourceIndex
from workload_graph.ids import ResourceKey
from workload_graph.models import (
    BuildResult,
    CandidateWorkload,
    Member,
    ProbeResult,
    ResidualGroup,
    SharedInfra,
    Unplaced,
)

T0 = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def _snap(status: str = "ok") -> object:
    return cost_snapshot("cost_explorer", ACCT_A, {}, status=status)


def test_cache_key_is_stable_and_changes_with_any_part() -> None:
    base = SnapshotCache.key("cost_explorer", ACCT_A, "all", {"a": 1}, date(2026, 11, 19))
    assert base == SnapshotCache.key("cost_explorer", ACCT_A, "all", {"a": 1},
                                     date(2026, 11, 19))
    for other in (
        SnapshotCache.key("cost_optimization_hub", ACCT_A, "all", {"a": 1}, date(2026, 11, 19)),
        SnapshotCache.key("cost_explorer", ACCT_B, "all", {"a": 1}, date(2026, 11, 19)),
        SnapshotCache.key("cost_explorer", ACCT_A, "x", {"a": 1}, date(2026, 11, 19)),
        SnapshotCache.key("cost_explorer", ACCT_A, "all", {"a": 2}, date(2026, 11, 19)),
        SnapshotCache.key("cost_explorer", ACCT_A, "all", {"a": 1}, date(2026, 11, 20)),
    ):
        assert other != base


def test_a_hit_within_the_ttl_does_not_call_the_collector(tmp_path: Path) -> None:
    clock = Clock()
    cache = SnapshotCache(tmp_path, clock, ttl_hours=24)
    calls: list[int] = []

    def collect() -> object:
        calls.append(1)
        return _snap()

    first, _, hit1 = cache.fetch("k", collect)  # type: ignore[arg-type]
    clock.now = T0 + timedelta(hours=23)
    second, _, hit2 = cache.fetch("k", collect)  # type: ignore[arg-type]
    assert calls == [1] and (hit1, hit2) == (False, True)
    assert second == first
    clock.now = T0 + timedelta(hours=24)
    cache.fetch("k", collect)  # type: ignore[arg-type]
    assert calls == [1, 1]


def test_an_error_snapshot_is_never_stored(tmp_path: Path) -> None:
    cache = SnapshotCache(tmp_path, Clock())
    assert cache.put("k", _snap("error")) is False  # type: ignore[arg-type]
    assert cache.get("k") == (None, [])


def test_a_corrupt_cache_file_is_ignored_with_a_note(tmp_path: Path) -> None:
    (tmp_path / "k.json").write_text("{broken")
    snapshot, notes = SnapshotCache(tmp_path, Clock()).get("k")
    assert snapshot is None and notes and "unreadable" in notes[0]


# --- F9 -------------------------------------------------------------------------------------

def _key(arn: str, account: str = ACCT_A, region: str = REGION) -> ResourceKey:
    return ResourceKey(account_id=account, region=region, arn=arn)


INSTANCE = _key(f"arn:aws:ec2:{REGION}:{ACCT_A}:instance/i-0abc")
ATTACHED = _key(f"arn:aws:ec2:{REGION}:{ACCT_A}:instance/i-0att")
BUCKET = _key("arn:aws:s3:::orders-bucket", region="global")
FUNCTION = _key(f"arn:aws:lambda:{REGION}:{ACCT_A}:function:orders-fn")
LB = _key(f"arn:aws:elasticloadbalancing:{REGION}:{ACCT_A}:loadbalancer/app/shared/1")
RESIDUAL = _key(f"arn:aws:ec2:{REGION}:{ACCT_A}:instance/i-0res")
UNMAPPED = _key(f"arn:aws:ec2:{REGION}:{ACCT_A}:instance/i-0unm")
DUP_ONE = _key(f"arn:aws:ec2:{REGION}:{ACCT_A}:volume/dup")
DUP_TWO = _key(f"arn:aws:ec2:{REGION}:{ACCT_A}:snapshot/dup")
OTHER_ACCOUNT = _key(f"arn:aws:ec2:{REGION}:{ACCT_B}:instance/i-0abc", account=ACCT_B)


def build_result(workloads: list[CandidateWorkload] | None = None) -> BuildResult:
    default = [CandidateWorkload(
        workload_id="w1", name_hint="orders", confidence="high", accounts=[ACCT_A],
        sources_used=["tags"], shared_dependencies=[], external_refs=[], evidence_summary={},
        members=[Member(key=k, membership=m, reasons=[], evidence=[]) for k, m in (
            (INSTANCE, "declared"), (ATTACHED, "attached"), (BUCKET, "declared"),
            (FUNCTION, "declared"), (DUP_ONE, "declared"), (DUP_TWO, "declared"))])]
    return BuildResult(
        ruleset_version="0.1.0", ruleset_hash="h", workloads=workloads or default,
        shared_infrastructure=[SharedInfra(key=LB, touches=["w1", "w2"])],
        residual_groups=[ResidualGroup(account_id=ACCT_A, region=REGION, vpc_id="vpc-1",
                                       members=[Unplaced(key=RESIDUAL, reason="no_edges")])],
        unmapped=[Unplaced(key=UNMAPPED, reason="ambiguous"),
                  Unplaced(key=OTHER_ACCOUNT, reason="no_edges")],
        external_refs=[], probe=ProbeResult(entries=[], notes=[]), notes=[], generated_from=[])


def test_matching_by_arn_and_physical_id() -> None:
    index = ResourceIndex(build_result())
    assert index.placement(ACCT_A, "i-0abc").workload_id == "w1"
    assert index.placement(ACCT_A, INSTANCE.arn).membership == "declared"
    assert index.placement(ACCT_A, "orders-bucket").kind == "workload"
    assert index.placement(ACCT_A, "orders-fn").kind == "workload"


def test_a_shared_physical_id_in_one_account_is_ambiguous_and_never_matches() -> None:
    index = ResourceIndex(build_result())
    assert index.placement(ACCT_A, "dup").kind == "ambiguous"
    assert index.resolve(ACCT_A, "dup") is None
    assert any("dup" in note for note in index.notes)
    assert index.placement(ACCT_A, DUP_ONE.arn).kind == "workload"


def test_the_same_physical_id_in_two_accounts_does_not_collide() -> None:
    index = ResourceIndex(build_result())
    assert index.placement(ACCT_A, "i-0abc").kind == "workload"
    assert index.placement(ACCT_B, "i-0abc").kind == "unplaced"


def test_every_placement_outcome() -> None:
    index = ResourceIndex(build_result())
    assert index.placement(ACCT_A, "i-0att").membership == "attached"
    assert index.placement(ACCT_A, LB.arn).kind == "shared"
    assert index.placement(ACCT_A, "i-0res").kind == "unplaced"
    assert index.placement(ACCT_A, "i-0unm").kind == "unplaced"
    assert index.placement(ACCT_A, "i-0nothere").kind == "unknown"


def test_members_of_unapproved_workloads_count_as_unplaced() -> None:
    index = ResourceIndex(build_result(), approved=[])
    assert index.placement(ACCT_A, "i-0abc").kind == "unplaced"
    assert index.workloads == {}
    assert index.account_resource_counts(ACCT_A) == (9, 9)
    assert ResourceIndex(build_result()).account_resource_counts(ACCT_A) == (3, 9)


def test_tags_are_looked_up_by_key() -> None:
    index = ResourceIndex(build_result(), tags={INSTANCE.key_str(): {"app": "orders"}})
    assert index.tags_of(INSTANCE) == {"app": "orders"}
    assert index.tags_of(BUCKET) == {}
