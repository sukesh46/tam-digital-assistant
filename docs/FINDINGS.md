# Cost findings

The cost layer answers one question for a TAM: for the workloads a human approved, what is the spend, what changed, and what can be saved? Every statement is cited.

## Reasoning chain

1. A workload is only useful in a cost review if its spend can be stated, so spend is attributed to approved workloads with an explicit method and confidence.
2. Account totals are authoritative, so every dollar of an account window lands in a named bucket. Nothing is silently dropped.
3. Month-over-month comparison uses complete months only. The current month is shown alone, marked estimated.
4. Savings come from Cost Optimization Hub. Its deduplicated total is the only savings total; individual recommendations are never added up as if they were independent.
5. Gaps are findings too: a source that was denied or not enabled produces a data-quality finding and a concrete ask for the customer.
6. Text is generated from findings, never the reverse. A guardrail rejects any sentence that is uncited, states a number the findings do not hold, makes a causal claim, or names an unknown workload.

## Pipeline

```
BuildResult (approved workloads) ─┐
graph snapshots (tags) ───────────┼─> ResourceIndex ─┐
cost_explorer.json ──> facts ─────┘                  ├─> attribution, drivers, savings,
cost_optimization_hub.json ──> recommendations ──────┘   data quality ─> ordered findings
                                                          ─> asks, talking points
                                                          ─> FindingsReport (+ narrative, guardrail)
```

Entry points: `workload_graph.pipeline_findings.run(...)` (in memory) and `workload-graph findings --build-result FILE --snapshots DIR --as-of YYYY-MM-DD [--findings-ruleset FILE] [--graph-snapshots DIR] --out FILE` (exit 0, or 2 on bad input). The date comes only from `--as-of`; no module in `cost/`, `findings/` or `report/` reads the clock.

## Finding kinds

| Kind | Rule ids | Notes |
|---|---|---|
| `spend_change` | `spend_change`, `spend_change_partial_month` | Per account, plus an aggregate (`account_id` null) when there is more than one account |
| `spend_driver` | `spend_driver` | Account-level services ranked by absolute change; flags `new_service`, `ended_service` |
| `workload_spend` | `workload_spend` | Monthly trend of a workload's tag series (two or more months) |
| `savings_opportunity` | `savings_opportunity` | One per resource recommendation; tier `quick_win` or `plan_first`; placement flags |
| `commitment_opportunity` | `commitment_opportunity` | Savings Plans and Reserved Instances; account-level only |
| `data_quality` | `source_unavailable`, `resource_level_not_enabled`, `tag_cost_not_visible`, `attribution_low_explained_ratio`, `unplaced_share_high`, `attribution_disagreement`, `savings_overlap`, `stale_recommendations`, `currency_mismatch`, `insufficient_history` | Gaps and caveats; most trigger an ask. A skipped tag method is a note on the workload's spend entry (`tag_ambiguous`, `tag_unavailable`), not a finding |

## Attribution ladder

| Method | Source | Window | Confidence |
|---|---|---|---|
| tag | an activated cost allocation tag shared by at least 80% of members, with one value | complete months | min(workload, medium) |
| resource_level | daily resource costs for the top services | trailing 13 days | min(workload, high); low if under half the window is explained |
| sole_account | the only approved workload in an account with few resources outside it | complete months | low |
| none | no method applies | none | none |

Tag is primary when present; resource-level is kept alongside it. When both exist and their daily averages differ by more than 25%, `attribution_disagreement` is raised.

Resource-level buckets per account: `workload:<id>`, `shared`, `unplaced`, `unknown`, `no_resource_id`, `unexplained`. They sum exactly to the account window total. Shared infrastructure is not allocated to workloads (allocation is a business decision). Members of workloads that were not approved count as `unplaced`.

## Guardrail

| Code | Rejects |
|---|---|
| G1 | a sentence with no cited finding |
| G2 | a citation of a finding that is not in the report |
| G3 | money, a percentage, or an integer of two or more digits that is not a rendered value of a cited finding |
| G4 | causal language (`because`, `due to`, `caused by`, `as a result of`, `thanks to`); `driven by` is allowed only when every cited finding is a `spend_driver` |
| G5 | `workload "<name>"` where the name is not a workload in the report |
| G6 | a sentence longer than 400 characters |

A section that fails is replaced by the template narrator's section, and the report's notes say so. The model-written narrator is an interface only.

## Reading the report

- `summary`: the headline figures, each with the finding ids it comes from.
- `account_level` and `workloads[*].findings`: finding ids in display order (`F-001`...).
- `buckets`: the reconciliation tables.
- `coverage`: per account and source, what was available (`available`, `empty`, `denied`, `not_enabled`, `partial`, `error`).
- `customer_asks`: what the customer can enable to close a gap.
- `evidence_index`: per finding, the snapshot id and JSON pointer of every raw datum it rests on.
- `notes`: degradations and narrative replacements.
