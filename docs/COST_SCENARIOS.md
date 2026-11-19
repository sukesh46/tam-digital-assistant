# Cost scenarios

All values are fake. The fixtures are built in code by `tests/wg_cost_builders.py` (`scenario()` is the standard single-account case; its docstring lists every hand-computed figure). No frozen JSON is kept: the builder is the single source, and the CLI test writes it to disk to exercise the file path.

| # | Scenario | Expected | Test |
|---|---|---|---|
| C1 | Tagged workload, tag cost active, recommendations match | Tag method primary; savings attached | `test_cost_scenarios.py::test_c1_*` |
| C2 | Resource-level data for top services | Buckets reconcile; method A with explained ratio | `test_c2_*`, `test_findings_report.py::test_buckets_reconcile_exactly` |
| C3 | No tags, no resource-level, two workloads in one account | Method `none`; savings still attach | `test_c3_*` |
| C4 | Sole workload in an account | Method C, confidence low, note | `test_c4_*`, `test_findings_attribution.py::test_sole_workload_*` |
| C5 | Cost Optimization Hub not opted in | `source_unavailable` (not_enabled), `A-COH-OPTIN`, no savings | `test_findings_report.py::test_hub_not_enabled_*` |
| C6 | Cost Explorer denied | Spend absent, coverage denied, `A-ACCESS`; savings present | `test_c6_*` |
| C7 | Item sum exceeds deduplicated total | `savings_overlap` with both numbers | `test_findings_rules.py::test_overlap_*` |
| C8 | Commitment recommendations | Account-level only, not in workload sums | `test_findings_rules.py::test_commitments_*` |
| C9 | New service appears | Driver with `new_service`, pct none | `test_findings_rules.py::test_drivers_flags_*` |
| C10 | Current month partial | Shown alone as estimated | `test_findings_rules.py::test_the_partial_month_*` |
| C11 | Shared load balancer with spend | `shared` bucket; no allocation | `test_findings_attribution.py::test_resource_level_buckets_*` |
| C12 | Recommendation on a resource not in the graph | Account-level, `resource_not_in_graph` | `test_findings_rules.py::test_every_placement_*` |
| C13 | Several accounts under one payer | Per account and aggregate correct | `test_findings_report.py::test_two_accounts_*`, `test_findings_rules.py::test_the_aggregate_*` |
| C14 | Currency mismatch | Not summed; `currency_mismatch` | `test_findings_rules.py::test_currency_mismatch_*`, `test_a_recommendation_in_another_currency_*` |
| C15 | Stale recommendations | One finding per account with a count | `test_findings_rules.py::test_stale_findings_*` |
| C16 | Members carry two tag values | Tag method skipped (note `tag_ambiguous`) | `test_findings_attribution.py::test_tag_method_is_skipped_when_members_carry_two_values` |
| C17 | Multi-page responses | Pages merged; totals unchanged | `test_c17_*` |
| C18 | Tag and resource-level disagree | `attribution_disagreement` on daily averages | `test_findings_attribution.py::test_disagreement_*` |

Invariants (in `test_cost_scenarios.py` and `test_findings_report.py`): buckets reconcile exactly for generated facts (hypothesis); any order of snapshots, workloads, facts and recommendations gives the same bytes (hypothesis); three `PYTHONHASHSEED` values give the same bytes; every citation resolves; the report has no JSON floats.
