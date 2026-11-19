"""Shared test fixtures: network block, frozen clock and fixture loaders."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import pytest
from wg_test_helpers import FROZEN_NOW, load_snapshot

from workload_graph.models import RawSnapshot


class NetworkAccessBlockedError(RuntimeError):
    """Raised when code under test attempts a network connection."""


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail any socket connection attempt; all tests must be offline."""

    def _refuse(*_args: Any, **_kwargs: Any) -> None:
        raise NetworkAccessBlockedError("network access is blocked in tests")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture
def frozen_now() -> datetime:
    """A fixed, timezone-aware 'now' for tests that need a clock."""
    return FROZEN_NOW


@pytest.fixture
def snapshot_loader() -> Callable[[str], RawSnapshot]:
    """Load a RawSnapshot relative to tests/fixtures/raw."""
    return load_snapshot
