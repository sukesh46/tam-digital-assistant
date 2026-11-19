"""Deterministic name hint for a candidate workload.

Status: partial
Purpose: a readable label: the most common workload tag value among members, else the most
    common stack name, else ``workload-<id>``. Ties break alphabetically.
Inputs: member resources, ruleset (workload tag keys), workload id.
Outputs: a name string.
Planned dependencies: none.
Notes: # SKELETON: naming will later be replaced by an LLM step behind an interface; the
    heuristic stays as the deterministic fallback.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from workload_graph.models import Resource
from workload_graph.ruleset import Ruleset


def _most_common(counter: Counter[str]) -> str | None:
    if not counter:
        return None
    return min(counter.items(), key=lambda item: (-item[1], item[0]))[0]


def name_hint(members: Iterable[Resource], ruleset: Ruleset, workload_id: str) -> str:
    """Return the name hint for a workload."""
    keys = set(ruleset.workload_tag_keys)
    tag_values: Counter[str] = Counter()
    stack_names: Counter[str] = Counter()
    for resource in members:
        values = {v for k, v in resource.tags.items() if k.lower() in keys and v}
        tag_values.update(values)
        stack = resource.attributes.get("stack_name")
        if stack:
            stack_names[stack] += 1
    return _most_common(tag_values) or _most_common(stack_names) or f"workload-{workload_id}"
