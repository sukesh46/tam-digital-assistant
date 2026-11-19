"""Pure normalisers from raw snapshots to resources and edges.

Status: real
Purpose: registry mapping each source to its normaliser.
Inputs: ``RawSnapshot``, ``Ruleset``.
Outputs: ``NormalisedBatch``.
Planned dependencies: none.
Notes: every normaliser is a pure function with the same signature.
"""

from workload_graph.models import RawSnapshot
from workload_graph.normalisers.base import NormalisedBatch, Normaliser
from workload_graph.normalisers.cloudtrail import normalise_cloudtrail
from workload_graph.normalisers.stacks import normalise_stacks
from workload_graph.normalisers.tags import normalise_tags
from workload_graph.normalisers.topology import normalise_topology
from workload_graph.ruleset import Ruleset

NORMALISERS: dict[str, Normaliser] = {
    "cloudtrail": normalise_cloudtrail,
    "stacks": normalise_stacks,
    "tags": normalise_tags,
    "topology": normalise_topology,
}


def normalise(snapshot: RawSnapshot, ruleset: Ruleset) -> NormalisedBatch:
    """Normalise a snapshot with the normaliser for its source."""
    return NORMALISERS[snapshot.source](snapshot, ruleset)


__all__ = ["NORMALISERS", "NormalisedBatch", "Normaliser", "normalise"]
