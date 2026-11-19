"""Every fixture page must match the real botocore response shape."""

from __future__ import annotations

from pathlib import Path

import pytest
from wg_test_helpers import RAW_DIR, load_snapshot, shape_errors

FIXTURE_FILES = sorted(
    p.relative_to(RAW_DIR).as_posix() for p in RAW_DIR.glob("*/*/*.json")
    if p.parent.name.isdigit() and len(p.parent.name) == 12
)


def test_there_are_fixture_files() -> None:
    assert FIXTURE_FILES


@pytest.mark.parametrize("relative", FIXTURE_FILES)
def test_fixture_pages_match_botocore_output_shapes(relative: str) -> None:
    snapshot = load_snapshot(relative)
    for operation, pages in sorted(snapshot.payload.items()):
        if operation.startswith("_"):
            continue
        for index, page in enumerate(pages):
            errors = shape_errors(operation, page)
            assert errors == "", f"{Path(relative)} {operation}[{index}]: {errors}"
