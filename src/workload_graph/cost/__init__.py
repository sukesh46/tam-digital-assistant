"""Cost layer primitives: money, periods, cost models and the snapshot cache.

Status: real
Purpose: shared building blocks for the cost collectors, normalisers and findings.
Inputs: none.
Outputs: submodules ``money``, ``periods``, ``models``, ``snapshots``, ``cache``.
Planned dependencies: none.
Notes: money is ``Decimal`` throughout; no wall-clock calls (the date comes from ``as_of``).
"""
