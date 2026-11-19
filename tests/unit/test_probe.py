"""Step 7: capability probe tests."""

from __future__ import annotations

import pytest
from wg_test_helpers import ACCT_A, ACCT_B, ACCT_C, make_snapshot

from workload_graph.models import RawSnapshot
from workload_graph.probe import probe_sources

WITH_DATA = {"GetResources": [{"ResourceTagMappingList": [
    {"ResourceARN": "arn:aws:s3:::a", "Tags": []}]}]}
NO_DATA = {"GetResources": [{"ResourceTagMappingList": []}]}


def _tags(status: str = "ok", payload: dict | None = None, account: str = ACCT_A,  # type: ignore[type-arg]
          region: str = "eu-west-1") -> RawSnapshot:
    return make_snapshot("tags", WITH_DATA if payload is None else payload, status=status,
                         account_id=account, region=region,
                         snapshot_id=f"tags-{account}-{region}")


def test_ok_with_data_is_available() -> None:
    result = probe_sources([_tags()], [ACCT_A], sources=["tags"])
    assert result.availability(ACCT_A, "tags") == "available"


def test_ok_without_data_is_empty() -> None:
    result = probe_sources([_tags(payload=NO_DATA)], [ACCT_A], sources=["tags"])
    assert result.availability(ACCT_A, "tags") == "empty"


@pytest.mark.parametrize("status", ["empty", "denied", "partial", "error"])
def test_other_statuses_map_to_their_namesake(status: str) -> None:
    result = probe_sources([_tags(status=status)], [ACCT_A], sources=["tags"])
    assert result.availability(ACCT_A, "tags") == status


def test_missing_snapshot_is_error_with_note() -> None:
    result = probe_sources([], [ACCT_A], sources=["tags"])
    entry = result.entries[0]
    assert entry.availability == "error"
    assert any("no snapshot" in n for n in entry.notes)


def test_accounts_and_sources_are_listed_in_sorted_order() -> None:
    result = probe_sources([_tags(account=ACCT_B), _tags()], [ACCT_B, ACCT_A])
    pairs = [(e.account_id, e.source) for e in result.entries]
    assert pairs == sorted(pairs)
    assert {a for a, _ in pairs} == {ACCT_A, ACCT_B}
    assert {s for _, s in pairs} == {"cloudtrail", "stacks", "tags", "topology"}


def test_mixed_regions_with_some_data_are_partial() -> None:
    result = probe_sources([_tags(), _tags(status="denied", region="us-east-1")], [ACCT_A],
                           sources=["tags"])
    entry = result.entries[0]
    assert entry.availability == "partial"
    assert any("us-east-1=denied" in n for n in entry.notes)


def test_mixed_regions_without_data_prefer_denied() -> None:
    result = probe_sources([_tags(payload=NO_DATA), _tags(status="denied", region="us-east-1")],
                           [ACCT_A], sources=["tags"])
    assert result.availability(ACCT_A, "tags") == "denied"


def test_same_status_in_every_region_is_kept() -> None:
    result = probe_sources([_tags(), _tags(region="us-east-1")], [ACCT_A], sources=["tags"])
    assert result.availability(ACCT_A, "tags") == "available"


def test_snapshots_for_accounts_outside_the_list_are_noted() -> None:
    result = probe_sources([_tags(account=ACCT_C)], [ACCT_A], sources=["tags"])
    assert any(ACCT_C in n for n in result.notes)
    assert result.availability(ACCT_C, "tags") is None


def test_probe_is_independent_of_input_order() -> None:
    snaps = [_tags(), _tags(status="denied", region="us-east-1"), _tags(account=ACCT_B)]
    assert probe_sources(snaps, [ACCT_A, ACCT_B]) == probe_sources(snaps[::-1], [ACCT_B, ACCT_A])
