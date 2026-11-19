"""Cost snapshots on disk and JSON-pointer resolution.

Status: real
Purpose: load cost snapshots with every JSON number as ``Decimal`` (F-D2), write them back without
    losing digits, and resolve evidence pointers to the datum they name.
Inputs: snapshot files ``<account-id>/<cost source>.json``; evidence references.
Outputs: ``load_cost_snapshot``, ``dump_cost_snapshot``, ``decimalise``, ``escape_pointer``,
    ``resolve_pointer``, ``resolve_ref``.
Planned dependencies: models.
Notes: the graph sources keep their existing loader. Only the cost sources come through here.
    Amounts are written back as strings, which ``parse_amount`` reads exactly.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from workload_graph.models import RawSnapshot


def decimalise(value: Any) -> Any:
    """Copy ``value`` with every binary float replaced by ``Decimal(str(f))``. Any: raw JSON."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: decimalise(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [decimalise(item) for item in value]
    return value


def load_cost_snapshot(path: Path) -> RawSnapshot:
    """Load a cost snapshot; JSON numbers with a fraction become ``Decimal``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal)
    return RawSnapshot.model_validate(data)


def _default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"cannot serialise {type(value).__name__}")


def dump_cost_snapshot(snapshot: RawSnapshot) -> str:
    """Sorted, indented JSON with a trailing newline. Decimals are written as strings."""
    return json.dumps(snapshot.model_dump(mode="python"), default=_default, sort_keys=True,
                      indent=2) + "\n"


def escape_pointer(token: str) -> str:
    """Escape one JSON-pointer token (RFC 6901)."""
    return token.replace("~", "~0").replace("/", "~1")


def resolve_pointer(snapshot: RawSnapshot, pointer: str) -> Any:
    """The datum at ``pointer`` inside ``snapshot``. ``KeyError`` if it does not exist."""
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    node: Any = snapshot.model_dump(mode="python")
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            if token not in node:
                raise KeyError(pointer)
            node = node[token]
        elif isinstance(node, list):
            if not token.isdigit() or int(token) >= len(node):
                raise KeyError(pointer)
            node = node[int(token)]
        else:
            raise KeyError(pointer)
    return node


def resolve_ref(snapshots: Mapping[str, RawSnapshot], raw_ref: str) -> Any:
    """Resolve ``'{snapshot_id}#{pointer}'`` against snapshots keyed by id."""
    snapshot_id, _, pointer = raw_ref.partition("#")
    if snapshot_id not in snapshots:
        raise KeyError(raw_ref)
    return resolve_pointer(snapshots[snapshot_id], pointer)
