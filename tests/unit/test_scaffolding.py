"""Step 1: scaffolding, toolchain and safety-net tests."""

from __future__ import annotations

import importlib
import re
import socket
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

MODULES = [
    "workload_graph",
    "workload_graph.ids",
    "workload_graph.models",
    "workload_graph.models.evidence",
    "workload_graph.models.resource",
    "workload_graph.models.edge",
    "workload_graph.models.snapshot",
    "workload_graph.models.probe",
    "workload_graph.models.workload",
    "workload_graph.models.workspace",
    "workload_graph.ruleset",
    "workload_graph.auth",
    "workload_graph.auth.session",
    "workload_graph.collectors",
    "workload_graph.collectors.base",
    "workload_graph.collectors.tags",
    "workload_graph.collectors.stacks",
    "workload_graph.collectors.topology",
    "workload_graph.collectors.cloudtrail",
    "workload_graph.normalisers",
    "workload_graph.normalisers.base",
    "workload_graph.normalisers.tags",
    "workload_graph.normalisers.stacks",
    "workload_graph.normalisers.topology",
    "workload_graph.normalisers.cloudtrail",
    "workload_graph.probe",
    "workload_graph.store",
    "workload_graph.store.base",
    "workload_graph.store.memory",
    "workload_graph.store.parquet",
    "workload_graph.graph",
    "workload_graph.graph.seeds",
    "workload_graph.graph.attach",
    "workload_graph.graph.shared",
    "workload_graph.graph.residual",
    "workload_graph.graph.crossaccount",
    "workload_graph.graph.confidence",
    "workload_graph.graph.naming",
    "workload_graph.graph.build",
    "workload_graph.evaluation",
    "workload_graph.evaluation.metrics",
    "workload_graph.evaluation.golden",
    "workload_graph.pipeline",
    "workload_graph.live",
    "workload_graph.credentials",
    "workload_graph.jobs",
    "workload_graph.fanout",
    "workload_graph.runs",
    "workload_graph.ui_server",
    "workload_graph.cli",
    "workload_graph.logging_setup",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_package_imports(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert module.__doc__, f"{module_name} must have a module docstring"
    assert "Status:" in module.__doc__, f"{module_name} docstring must declare a Status"


def test_python_is_3_11() -> None:
    assert sys.version_info[:2] == (3, 11)


def test_network_access_is_blocked() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="network access is blocked"):
            sock.connect(("192.0.2.1", 443))
    finally:
        sock.close()


def test_no_forbidden_word_in_source() -> None:
    forbidden = re.compile("cli" + "ents", re.IGNORECASE)
    offenders: list[str] = []
    for folder in ("src", "tests", "docs"):
        for path in sorted((PROJECT_ROOT / folder).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix not in {".py", ".md", ".json", ".txt", ".toml"}:
                continue
            if forbidden.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == []
