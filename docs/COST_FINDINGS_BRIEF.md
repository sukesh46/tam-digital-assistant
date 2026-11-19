# Cost findings brief (saved verbatim)

Source: the brief the TAM supplied on 19 Nov 2026, saved unedited so it is versioned with the code. Treat it as the authoritative spec for the cost layer. Build steps in section 11 are not started. Two checklist lines that quoted the forbidden word were reworded so the docs guard passes; the rule is unchanged.

---

## CURSOR INSTRUCTIONS: Workload-aware cost findings and the structured, cited report

Read this whole file before touching any code. Re-read the relevant section before each step.
This extends the **existing `workload-graph` codebase**. It does not start a new project.

---

## 0. Purpose, scope, hard rules

### 0.1 What you are building
A new layer on top of the existing workload graph. Given (a) the existing `BuildResult` (candidate workloads, shared infrastructure, residual groups, unmapped resources) and (b) read-only **Cost Explorer** and **Cost Optimization Hub** data, it produces one **structured, cited findings report** that reads like a TAM's cost review:

- where the money goes and what changed,
- what to do first (savings opportunities, tiered by effort),
- how much of the spend could and could not be attributed to each workload, and why,
- what data was missing and what to ask the customer to enable,
- every statement traceable to a finding, and every finding traceable to the raw API response it came from.

### 0.2 In scope
Cost Explorer and Cost Optimization Hub collectors (read-only, Stubber-tested), pure normalisers, spend attribution to workloads, deterministic analysis rules (spend drivers, savings, data quality), finding records, customer asks and talking points (template-based), a narrative layer with a **guardrail**, report assembly, a snapshot cache, a CLI command that runs offline, and an evaluation-style golden test suite.

### 0.3 Out of scope (do NOT build; add a line to `docs/OPEN_QUESTIONS.md` if tempted)
Trusted Advisor, AWS Support cases, AWS Health, Security Hub, Cost Anomaly Detection, Well-Architected Tool, meeting notes or CRM data, DOCX/PDF rendering, any UI work, live Bedrock/LLM calls, scheduling, deployment infrastructure, anything that writes to AWS, Ingram invoice or discount reconciliation.

### 0.4 Hard rules (never break)
1. **Read-only toward AWS.** No write operations anywhere. This includes Cost Optimization Hub operations such as updating enrolment status or preferences. A test enforces the allowlist (Step F6/F7).
2. **No live AWS, network or LLM calls** in code that tests run, and none in tests. All tests pass offline. The existing autouse network-blocking fixture must stay in place.
3. **No credentials, real account IDs, real ARNs or customer data.** Fake values only (`111111111111`, `222222222222`, `example-customer`).
4. **Money is `Decimal`, never `float`.** Never use `float` for amounts, ratios or percentages anywhere in `src/`. Details in Section 8.2. A test scans `src/` for `float(` and `: float` in the new packages.
5. **Deterministic output.** Same inputs, same rulesets and the same `as_of` date give byte-identical output regardless of input order or `PYTHONHASHSEED`. No wall-clock calls in logic: the date comes from an injected `as_of`.
6. **No invented facts or numbers.** If you are unsure how an AWS API behaves, write `# ASSUMPTION:` in code and add it to `docs/ASSUMPTIONS.md`. Never silently guess. Never invent weights, thresholds or formulas beyond those given here. Every default in Section 9 is provisional and marked as such.
7. **No causal claims the data cannot support.** Cost Explorer at service level tells us *what* changed, not *why*. The report may say a service's spend rose; it may not say the rise was caused by something unless a finding carries evidence for it (none do in this version).
8. **Additive changes to existing code.** Do not delete or rewrite existing modules. The only permitted edits to existing files are listed in Step F0.3. Record each in `docs/DECISIONS.md`.
9. Use the word **"customers"**. Never the plural of the boto3 word for a service object (the forbidden word is in CURSOR_INSTRUCTIONS.md, section 0).
10. When something is ambiguous, record it in `docs/OPEN_QUESTIONS.md`, take the safest and most reversible option, and mention it in your final report. Do not silently guess.
11. Do not create, modify or delete anything outside the project directory. Do not install anything globally. Do not commit or push unless the user asks.

---

## 1. The reasoning behind this design (follow this chain)

1. **The workload map is the organising frame.** A TAM wants "what is happening in each workload", not just an account total. So every finding is attached to a workload where evidence allows, and to an explicit account-level bucket where it does not. We never guess an attachment.
2. **The two sources answer different questions.** Cost Explorer reports *spend* grouped by dimensions or tags. Cost Optimization Hub reports *recommendations about specific resources*. Spend is a **fact**. A recommendation is a **finding**. A judgement derived from facts ("spend rose materially") is also a **finding**, produced by a versioned, deterministic rule.
3. **Recommendations join to workloads exactly; spend does not.** Recommendations name resource ARNs, so they match the graph exactly. Cost Explorer spend is aggregated by service, account or tag. Per-workload spend exists only if the customer activated a tag, or enabled resource-level data (opt-in, daily, a short recent window, selected services, top 5,000 resources per service). So spend attribution is a ladder of methods, each with its own strength, and the report must say which method was used.
4. **Honesty beats completeness.** Attributed spend + shared + unplaced + unknown + unexplained must **equal the account total exactly**. The "unexplained" line is how the report stays truthful about what it could not see.
5. **Savings are not additive.** Recommendations can overlap (for example a rightsizing and a Savings Plan on the same compute). Cost Optimization Hub provides a **deduplicated** total. We use it as the authoritative account total and compare it to the sum of items. A mismatch becomes a data-quality finding. Commitment purchases (Savings Plans, Reserved Instances) belong to the account, never to a workload.
6. **Prioritise without inventing weights.** There is no cross-kind scoring formula. Sections appear in a fixed order. Within a section, findings sort by money at stake, then confidence, then ID. This is explainable and needs no tuning to be defensible.
7. **Confidence is inherited, never inflated.** A finding's confidence is the **minimum** of the workload's confidence and the attribution method's strength.
8. **The LLM comes last and decides nothing.** It may only phrase already-computed findings. A guardrail rejects any sentence without valid citations, with a number that does not match a cited finding, or with a causal claim. Rejected sections fall back to deterministic template text, and the fallback is recorded in the report notes, never silent. No live LLM call is built in this task.
9. **Pure functions in the middle.** Collectors do I/O and return raw snapshots. Normalisers, attribution, analysis and report assembly are pure. That is what makes everything testable offline and reproducible.
10. **Evidence is a pointer, not a copy.** Every finding carries pointers into the raw snapshots (snapshot ID plus JSON pointer). Tests prove each pointer resolves to the datum it claims to.
11. **Tell the customer what to turn on.** Most gaps are caused by settings the customer controls. The report converts each gap into a specific, template-based ask. That is what a TAM would do.

---

## 2. Decisions and their reasons (record each in `docs/DECISIONS.md`, status `provisional`)

| # | Decision | Reason | Revisit when |
|---|---|---|---|
| F-D1 | `Decimal` everywhere for money, ratios, percentages | Floats corrupt sums and break exact reconciliation | Never |
| F-D2 | Load cost snapshots with `json.loads(..., parse_float=Decimal)` | boto3 parses JSON doubles as floats; Cost Optimization Hub returns doubles | Never |
| F-D3 | Cents rounding happens only at presentation (`ROUND_HALF_UP`); sums use unrounded values | Avoids accumulated rounding error | Never |
| F-D4 | Separate `FindingsRuleset` with its own version and hash | Leaves the workload `Ruleset` untouched; both hashes appear in the report | Rulesets merge |
| F-D5 | Spend attribution ladder: tag (monthly) > resource-level (short window) > sole-workload account > none | Strongest and longest window first; both shown when both exist | Evidence of better ordering |
| F-D6 | Reconciliation buckets: workload, shared, unplaced, unknown, no_resource_id, unexplained | Makes every dollar of the account total land somewhere | None |
| F-D7 | No cost allocation to shared infrastructure | An allocation rule is a business decision; `# DECISION-PENDING:` | Customers specify a rule |
| F-D8 | Latest *complete* months only for month-over-month; current month shown separately as estimated | Partial months mislead | None |
| F-D9 | Account-level spend drivers only; workload-level drivers out of scope | Cost Explorer allows two group-bys, so workload plus service plus account cannot be combined | Resource-level data becomes reliable |
| F-D10 | Commitment recommendations (Savings Plans, Reserved Instances) are account-level | They are purchases, not resource changes | None |
| F-D11 | Use the hub's deduplicated summary as the authoritative savings total | Item sums can double count | None |
| F-D12 | Lexicographic ordering inside sections; no weighted score | No invented weights | TAM feedback |
| F-D13 | Confidence = min(workload confidence, method confidence) | Never inflates | Calibration data |
| F-D14 | Guardrail blocks all causal language in this version | No finding supports a cause | A finding type gains causal evidence |
| F-D15 | Template narrator is the default and the fallback; LLM narrator is an interface plus a stub | Deterministic, testable, safe | LLM step approved |
| F-D16 | Snapshot cache keyed by hash of (source, access account, call label, params, `as_of`) with 24-hour TTL (injected clock) | Limits per-request cost and latency | Cost Explorer pricing changes |
| F-D17 | Resource-level window default 13 days (not 14) | Documented limit is 14 days; inclusive/exclusive edge semantics unverified, so keep a margin | The bound is verified |

---

## 3. What is verified and what is not

**Verified against AWS documentation (November 2026), safe to rely on:**
- `GetCostAndUsage` and `GetCostAndUsageWithResources` responses contain `ResultsByTime[]` with `TimePeriod{Start,End}`, `Estimated`, `Groups[]{Keys[], Metrics{<name>{Amount(string), Unit}}}`, `Total`, `GroupDefinitions[]{Key,Type}`, and `NextPageToken`.
- Resource-level data is **opt-in**, enabled by the management account in Cost Management preferences, per selected service. It covers **daily granularity for the past 14 days** (hourly only for EC2 instances), appears within 48 hours, shows the **top 5,000 most costly resources per service**, and is switched off if unused for three consecutive months. It is not available in Savings Plans and Reservations coverage and utilisation reports.
- Tags must be **activated as cost allocation tags** before they appear in Cost Explorer. A backfill can cover up to 12 months, but only for periods when the tag was actually on the resource.
- Cost Optimization Hub is **opt-in**. A management account can enrol itself only, or itself and all members. One delegated administrator is allowed. `ListRecommendations` accepts filters including `accountIds`, `actionTypes`, `implementationEfforts`, `regions`, `resourceArns`, `resourceIds`, `resourceTypes`, `restartNeeded`, `rollbackPossible`, `tags`, plus `includeAllRecommendations`, `maxResults`, `nextToken`, `orderBy`. `ListRecommendationSummaries` returns `currencyCode`, `estimatedTotalDedupedSavings`, `groupBy`, `items[]{estimatedMonthlySavings, group, recommendationCount}`, `metrics.savingsPercentage`, `nextToken`.
- A recommendation carries (documented on `GetRecommendation`): `accountId`, `actionType`, `currencyCode`, `currentResourceType`, `estimatedMonthlyCost`, `estimatedMonthlySavings`, `estimatedSavingsPercentage`, `implementationEffort`, `lastRefreshTimestamp`, `recommendationId`, and more.

**NOT verified. Write `# ASSUMPTION:` at each use, list in `docs/ASSUMPTIONS.md`, and keep each behind a single, easily changed function:**
1. Tag group keys in Cost Explorer responses look like `key$value`, and an untagged group looks like `key$`.
2. `GroupBy` allows at most two group definitions per call.
3. `GetCostAndUsageWithResources` may require a `Filter` (for example on `SERVICE`) and a window of at most 14 days.
4. The error raised when resource-level data is not enabled, and the error raised when Cost Optimization Hub is not opted in, are unknown. Implement a **configurable matcher** (error code and message substrings) and map matches to status `not_enabled`.
5. `ListRecommendations` items contain `resourceArn`, `resourceId`, `region`, `recommendedResourceType`, `restartNeeded`, `rollbackPossible`, `source`, `tags`, as well as the fields in Section 3 above. Verify against botocore's service model (see F7).
6. `includeAllRecommendations=false` returns one recommendation per resource (the best), reducing overlap.
7. `implementationEffort` values are `VeryLow`, `Low`, `Medium`, `High`, `VeryHigh`.
8. `actionType` values include `Rightsize`, `Stop`, `Upgrade`, `PurchaseSavingsPlans`, `PurchaseReservedInstances`, `MigrateToGraviton`, `ScaleIn`, `Delete`. The commitment ones are `PurchaseSavingsPlans` and `PurchaseReservedInstances`.
9. The literal that Cost Explorer uses for costs with no resource ID is `NoResourceId`.
10. Cost Explorer and Cost Optimization Hub APIs are called against the `us-east-1` endpoint.
11. `ListRecommendationSummaries` supports `groupBy` of `AccountId` and `ActionType`.
12. Cost Explorer charges per paginated request. Not re-verified; the call budget and the cache exist because of this.

For each assumption, add a test that pins the current behaviour of your isolated function so a later correction is a one-line change.

---

## 4. Step F0: orient yourself in the existing codebase (do this before writing anything)

### F0.1 Read, in this order
`README.md`, `docs/DECISIONS.md`, `docs/ASSUMPTIONS.md`, `docs/OPEN_QUESTIONS.md`, `docs/STATUS.md`, then the source: `ids.py`, `models/` (evidence, resource, edge, snapshot, probe, workload), `ruleset.py`, `collectors/base.py` and one concrete collector, `normalisers/base.py` and one concrete normaliser, `pipeline.py`, `cli.py`, `graph/build.py`, `evaluation/`, and `tests/conftest.py` plus the existing fixtures.

### F0.2 Baseline
Activate the existing venv (`source .venv/bin/activate`). Run `make check`. **If it is not green, stop and report; do not proceed.** Record the baseline test count and coverage in `docs/STATUS.md`.

Confirm Python is 3.11 (`python --version`). Do not change versions.

### F0.3 The only permitted edits to existing files
1. Add `cost_explorer` and `cost_optimization_hub` to the source-name enum and to the pipeline's known-source list.
2. In the snapshot loader, add a Decimal-aware loading path used **only** for the two new sources (`parse_float=Decimal`). Existing sources keep their current loading.
3. Register the new CLI sub-command in `cli.py`.
4. Add the new boto3 stub extras to `requirements/dev.txt`: `boto3-stubs[ce,cost-optimization-hub]` (alongside the existing extras).
5. Add new docs and update `docs/STATUS.md`, `docs/DECISIONS.md`, `docs/ASSUMPTIONS.md`, `docs/OPEN_QUESTIONS.md`.

If you need any other change to an existing file, make the smallest possible change, and record why in `docs/DECISIONS.md`.

### F0.4 Match existing conventions
Frozen pydantic models, sorted deterministic JSON, `BaseCollector` plus `CallRecorder`, allowlisted operations, `botocore.stub.Stubber` for AWS tests, `# SKELETON:` / `# ASSUMPTION:` / `# TODO(input-needed):` / `# DECISION-PENDING:` / `# RISK:` markers, module docstrings with a `Status:` line. Where this file's naming differs from the existing code, **follow the existing code** and note the difference in `docs/DECISIONS.md`.

---

## 5. Environment

Reuse the existing `.venv` (Python 3.11). Add **no new runtime dependency**. Use only the standard library for dates, decimals, hashing and JSON. Do not add pandas or dateutil (month arithmetic is implemented and tested in Step F1).

Add the dev-only stub extras (Step F0.3.4) and run `pip install -r requirements/dev.txt`. Confirm that botocore ships the service models `ce` and `cost-optimization-hub` (`python -c "import botocore.session as s; print(s.get_session().get_available_services())"`); if either is missing, stop and report.

---

## 6. New layout (additions only)

```
src/workload_graph/
├── cost/
│   ├── __init__.py
│   ├── money.py             # parse_amount, quantize_cents, format_money, format_pct
│   ├── periods.py           # Period, month math, complete_months, windows
│   ├── models.py            # CostFact, CostScope, AccessPlan
│   └── cache.py             # SnapshotCache (injected clock)
├── collectors/
│   ├── cost_explorer.py     # CostExplorerCollector
│   └── cost_optimization_hub.py   # CostOptimizationHubCollector
├── normalisers/
│   ├── cost_explorer.py
│   └── cost_optimization_hub.py   # also defines Recommendation model
├── findings/
│   ├── __init__.py
│   ├── ruleset.py           # FindingsRuleset
│   ├── models.py            # Finding, FindingKind, Confidence
│   ├── index.py             # ResourceIndex, Placement
│   ├── attribution.py
│   ├── drivers.py
│   ├── savings.py
│   ├── data_quality.py
│   ├── asks.py              # customer asks and talking points (templates)
│   └── build.py             # build_findings(...)
├── report/
│   ├── __init__.py
│   ├── models.py            # FindingsReport and sections
│   ├── narrative.py         # Narrator protocol, TemplateNarrator, stubs
│   ├── guardrail.py
│   └── assemble.py
├── pipeline_findings.py
└── cli.py                   # (edited) adds `findings` command
docs/
├── IAM_PERMISSIONS.md  FINDINGS.md  COST_SCENARIOS.md
tests/
├── unit/cost/  unit/collectors/  unit/normalisers/  unit/findings/  unit/report/
├── scenarios_cost/test_cost_scenarios.py
├── determinism_cost/test_determinism.py  test_invariants.py
└── fixtures/
    ├── builders.py          # programmatic BuildResult and snapshot builders
    ├── cost/<scenario>/<access-account>/{cost_explorer.json,cost_optimization_hub.json}
    └── cost_golden/<scenario>.json
```

Every new `.py` file starts with the module docstring used in the existing code (summary, `Status:`, purpose, inputs, outputs, dependencies, notes), has full type hints and docstrings.

---

## 7. Domain model additions (all frozen pydantic models, JSON round-trip tested)

### 7.1 `cost/models.py`
- `AccessPlan`: `access_account_id` (the account whose credentials make the calls), `linked_account_ids` (accounts whose spend is requested). Two modes: **per-account** `(A, [A])`, and **payer** `(P, [A, B, C])`. Both must work identically downstream. `# DECISION-PENDING:` which mode the customer uses depends on who owns the management account (open question).
- `CostScope`: `as_of: date`, `lookback_months: int`, `metric: str`, `max_calls: int`, `resource_level_window_days: int`, `resource_level_top_services: int`, `tag_keys: tuple[str, ...]`.
- `CostFact`: `fact_id`, `kind` (`account_service_month`, `account_window_total`, `tag_month`, `resource_day`), `dimensions: dict[str, str]` (sorted keys, for example `{"linked_account": "...", "service": "..."}`), `period: Period`, `amount: Decimal`, `unit: str`, `estimated: bool`, `evidence: Evidence`.

### 7.2 `normalisers/cost_optimization_hub.py`
`Recommendation`: `recommendation_id`, `account_id`, `region`, `resource_arn | None`, `resource_id | None`, `current_resource_type`, `recommended_resource_type | None`, `action_type`, `estimated_monthly_savings: Decimal`, `estimated_monthly_cost: Decimal | None`, `estimated_savings_percentage: Decimal | None`, `currency: str`, `implementation_effort | None`, `restart_needed: bool | None`, `rollback_possible: bool | None`, `last_refresh: datetime | None`, `evidence: Evidence`. Missing optional fields stay `None`; never default them to a convenient value.

### 7.3 `findings/models.py`
- `Confidence` enum `high | medium | low` with rank `3|2|1` and `min_confidence(a, b)`.
- `FindingKind`: `spend_change`, `spend_driver`, `workload_spend`, `savings_opportunity`, `commitment_opportunity`, `data_quality`.
- `Finding`: `finding_id` (`F-` plus first 12 hex of SHA-256 over canonical JSON of rule id, rule version, kind, workload id, account id and the subject keys), `display_id` (assigned after the final stable sort: `F-001`, `F-002`, ... used only for presentation), `kind`, `rule_id`, `rule_version`, `pillar` (fixed `cost_optimization`), `workload_id | None`, `account_id | None`, `confidence`, `tier | None` (`quick_win` or `plan_first` for savings), `values: dict[str, str | int | bool | None]` (Decimals as strings), `evidence: tuple[Evidence, ...]` (at least one, except `data_quality` findings that describe an absence, which must carry the evidence of the response or error that showed the absence), `flags: tuple[str, ...]` (sorted), `supports_causal_claim: bool = False`.
- `Finding.rendered_values()` returns the set of strings the guardrail will accept for this finding (formatted money, percentages, integers).

### 7.4 `report/models.py`
`FindingsReport` (serialises with sorted keys; Decimals as strings; `schema_version = "1"`):
- `generated_from`: `build_result_sha256`, `workload_ruleset_hash`, `findings_ruleset_hash`, `snapshot_ids` (sorted), `as_of`.
- `scope`: accounts, months covered, currency, metric.
- `coverage`: per account and source, one of `available | empty | denied | not_enabled | partial | error`, plus flags `tag_cost_visible`, `resource_level_visible`.
- `summary`: latest complete month total, prior month total, delta, percent (or null), deduplicated savings total, quick-win total, share of spend unattributed. Each figure lists the finding IDs it comes from.
- `account_level`: findings with no workload.
- `workloads`: for each workload: `workload_id`, `name_hint`, `confidence`, `accounts`, `spend` (series of `WorkloadSpend` entries, one marked `primary`), `findings`.
- `buckets`: the reconciliation table per account and window.
- `customer_asks`, `talking_points`.
- `narrative`: sections of sentences; each sentence is `{text, finding_ids}`.
- `findings`: the flat sorted list.
- `evidence_index`: finding ID → evidence list (source, operation or label, snapshot ID, JSON pointer, data date).
- `notes`: sorted list including every degradation and every guardrail fallback.

---

## 8. Cross-cutting rules for the new code

### 8.1 Determinism checklist
Sort every set or dict before iterating if it can affect output. Never depend on set order. No `random`. No wall-clock calls (`datetime.now`, `date.today`, `time.time`) in `cost/`, `findings/`, `report/`; a test greps for them. The date always comes from `as_of`. JSON uses `sort_keys=True`, a stable list order, and a trailing newline.

### 8.2 Money and numbers (`cost/money.py`)
- `parse_amount(value)`: accepts `str`, `int`, `Decimal`; for a `float`, converts through `Decimal(str(value))`. Rejects `NaN`, `Infinity`, and `bool`. Negative amounts are valid (credits).
- `quantize_cents(d)`: `ROUND_HALF_UP` to `0.01`. Only used for presentation and for report summary figures; sums in analysis use unrounded values.
- `format_money(amount, unit)`: `"USD 6,900.00"` (unit, one space, thousands separators, two decimals; negative as `"-USD 120.00"`).
- `format_pct(ratio)`: one decimal place, `ROUND_HALF_UP`, `"9.2%"`; `None` renders as `"n/a"`.
- `safe_ratio(numerator, denominator)`: returns `None` if the denominator is zero.
- If two facts to be combined have different units, do **not** sum them. Raise a typed `CurrencyMismatch` that the caller turns into a data-quality finding (Step F13).

### 8.3 Safety
Collectors may call only allowlisted operations. Never log credentials or full API payloads. Error notes carry only the operation name and the error code.

### 8.4 Markers
`# SKELETON:` `# TODO(input-needed):` `# ASSUMPTION:` `# DECISION-PENDING:` `# RISK:`, each followed by a one-line explanation. Every provisional default in Section 9 carries `# SKELETON: provisional`.

---

## 9. Findings ruleset (`findings/ruleset.py`): all defaults PROVISIONAL

Frozen, validated pydantic model with `version`, JSON load and save, and `content_hash()` over canonical JSON (same approach as the existing `Ruleset`). All numeric values are `Decimal` or `int`.

| Field | Default | Meaning |
|---|---|---|
| `lookback_months` | `3` | Complete months of history to request |
| `metric` | `"UnblendedCost"` | Cost Explorer metric. `# DECISION-PENDING:` amortised cost matters for commitments |
| `driver_top_n` | `3` | Maximum drivers reported per scope |
| `driver_min_abs` | `"100.00"` | Minimum absolute monthly change for a driver |
| `resource_level_window_days` | `13` | See F-D17 |
| `resource_level_top_services` | `5` | Services queried for resource-level costs, chosen by latest-month spend |
| `tag_min_member_share` | `"0.8"` | Share of declared members that must carry the workload's tag value |
| `sole_workload_max_unplaced_ratio` | `"0.2"` | Maximum share of an account's resources outside the workload for the sole-workload method |
| `attribution_low_explained_ratio` | `"0.5"` | Below this, attribution confidence is capped at `low` |
| `attribution_disagreement_ratio` | `"0.25"` | Relative difference between two attribution methods that triggers a data-quality finding |
| `unplaced_share_flag` | `"0.3"` | Share of account spend not attributable that triggers a data-quality finding |
| `quick_win_efforts` | `["VeryLow", "Low"]` | Efforts eligible for the quick-win tier |
| `commitment_action_types` | `["PurchaseSavingsPlans", "PurchaseReservedInstances"]` | Account-level, never attributed to a workload |
| `stale_recommendation_days` | `14` | Age after which a recommendation counts as stale |
| `savings_dedupe_tolerance` | `"0.01"` | Allowed difference between item sum and deduplicated total |
| `method_confidence` | `{"tag": "medium", "resource_level": "high", "sole_account": "low"}` | Starting confidence by attribution method |
| `not_enabled_matchers` | See assumption 4 | Error codes and message substrings that map to `not_enabled` |

Validation rules: Decimals within sensible ranges (ratios in `[0, 1]`); lists lowercase-insensitive where relevant; `quick_win_efforts` and `commitment_action_types` non-overlapping with nothing; reject unknown keys.

---

## 10. Algorithms (specification; implement exactly, test every branch)

### 10.1 Periods (`cost/periods.py`)
- `Period(start: date, end: date)`; `end` is **exclusive**, matching Cost Explorer. Reject `end <= start`.
- `complete_months(as_of, n)` returns the `n` most recent complete calendar months before the month containing `as_of`, oldest first. For `as_of = 2026-11-19` and `n = 3`: Aug, Sep, Oct 2026.
- `current_partial_month(as_of)` returns `month_start(as_of)` to `as_of` (exclusive), or `None` when `as_of` is the first of the month.
- `trailing_window(as_of, days)` returns `as_of - days` to `as_of`.
- Month arithmetic is hand-written (`add_months`, `month_start`, `month_end_exclusive`) and handles year boundaries and February.

### 10.2 Collection plan (what each collector calls)
Each call has a **label**. The raw snapshot's payload is keyed by label (`{label: {"operation": ..., "params": ..., "pages": [...]}}`). Follow the existing `RawSnapshot` structure; if it only supports operation names as keys, use `"<Operation>:<label>"`.

**Cost Explorer** (access account per `AccessPlan`, filter `LINKED_ACCOUNT in linked_account_ids`):
1. `spend_by_service_account_monthly`: `GetCostAndUsage`, MONTHLY, window from the first complete month to `as_of`, `GroupBy` = `[SERVICE, LINKED_ACCOUNT]`, one metric.
2. `window_total_by_account`: `GetCostAndUsage`, DAILY, over the resource-level window, `GroupBy` = `[LINKED_ACCOUNT]`. Needed so the reconciliation uses totals for the identical window.
3. `tag_keys_seen`: `GetTags` over the lookback window, to learn which tag keys have cost data.
4. `spend_by_tag_monthly:<key>`: one call per key in `scope.tag_keys` that appeared in step 3, `GroupBy` = `[TAG:<key>, LINKED_ACCOUNT]`.
5. `resource_costs_daily:<service>`: `GetCostAndUsageWithResources`, DAILY, over the resource-level window, filtered on `SERVICE = <service>`, `GroupBy` = `[RESOURCE_ID, LINKED_ACCOUNT]`, for each of the top `resource_level_top_services` services (taken from the latest complete month in call 1). Skipped with a note if call 1 failed.

The collector never exceeds `scope.max_calls`; retries count; exceeding it yields status `partial`. Pagination follows `NextPageToken`. A denied call is recorded and does not abort the others. A call whose error matches `not_enabled_matchers` records status `not_enabled` for that label.

**Cost Optimization Hub** (access account per `AccessPlan`):
1. `enrolment` (only if botocore's model has `ListEnrollmentStatuses`; check `client.meta.service_model.operation_names`).
2. `list_recommendations`: `ListRecommendations`, filter `accountIds = linked_account_ids`, `includeAllRecommendations = False`, paginated by `nextToken`.
3. `summary_account:<id>`: `ListRecommendationSummaries` per linked account with `groupBy = "ActionType"` and a filter on that account, to obtain `estimatedTotalDedupedSavings`.

**Allowlists** (names must also pass the existing forbidden-prefix test): Cost Explorer: `GetCostAndUsage`, `GetCostAndUsageWithResources`, `GetTags`, `GetCostForecast` (only if Step F18 is built). Cost Optimization Hub: `ListRecommendations`, `ListRecommendationSummaries`, `ListEnrollmentStatuses`, `GetRecommendation`, `GetPreferences`. Explicitly **not** allowed: anything starting with `Update`, `Create`, `Delete`, `Put`, and specifically `UpdateEnrollmentStatus` and `UpdatePreferences`.

### 10.3 Normalising Cost Explorer (pure)
For each label, map each `Group.Keys` position to a dimension name using the **response's** `GroupDefinitions` (never the request). For tag groups, split `Keys[i]` on the **first** `$` into key and value; an empty value means untagged (kept as a fact with value `""`). Amounts: `parse_amount(Amount)`; the unit comes from `Unit`; `estimated` from `ResultsByTime[].Estimated`. Skip groups with missing metrics (note it). For daily window calls, keep daily facts and also produce one `account_window_total` per account (sum of the days, same unit). Evidence pointer example: `/payload/spend_by_service_account_monthly/pages/0/ResultsByTime/2/Groups/5`. Output is sorted and de-duplicated. Malformed rows are skipped with a note, never an exception.

### 10.4 Normalising Cost Optimization Hub (pure)
Produce `Recommendation` records (Section 7.2). Money through `parse_amount`. A recommendation without `resource_arn` and without `resource_id` is kept (it may be a commitment purchase) with both fields `None`. Evidence pointer example: `/payload/list_recommendations/pages/0/items/3`. Also parse the summaries into `(account_id, deduped_total, currency)`; if a summary lacks a deduplicated total, record a note, not a zero.

### 10.5 Resource index and placement (`findings/index.py`)
Build `ResourceIndex` from the store's resources and the `BuildResult`:
- For every resource, candidate ids are: its full ARN, and its **physical id** (the substring after the last `/` or `:` in the ARN).
- Index key is `(account_id, id)`. If two distinct resource keys share a key, mark that key **ambiguous**; ambiguous ids never match (note it).
- `placement(account_id, id_or_arn)` returns one of `Workload(workload_id, membership)`, `Shared`, `Unplaced`, `Unknown`, `Ambiguous`.
- Placement uses the `BuildResult`: members of workloads (with `declared`/`attached`), `shared_infrastructure`, `residual_groups` and `unmapped` (both are `Unplaced`), and anything not in the graph is `Unknown`.

### 10.6 Spend attribution (`findings/attribution.py`)
Produces `WorkloadSpend` entries per workload and a bucket table per `(account, window)`.

**Method A: resource-level** (only where `resource_day` facts exist). Classify each fact by `placement`. `resource_id == "NoResourceId"` goes to bucket `no_resource_id`. Buckets: `workload:<id>`, `shared`, `unplaced`, `unknown`, `no_resource_id`, and `unexplained` (computed as the account window total minus the sum of all explicit rows, so it absorbs services not queried, resources beyond the top 5,000, and anything else not itemised). **Invariant: the sum of all buckets equals the account window total exactly** (`Decimal` equality). `explained_ratio = (total - unexplained) / total` (`None` if total is zero). Workload amount is the sum of its bucket rows across the accounts it spans.

**Method B: tag** (only where `tag_month` facts exist). Resolve one `(key, value)` per workload: look at the tags of its **declared** members; for each key in the workload ruleset's `workload_tag_keys`, collect the values. A pair qualifies when exactly one value exists for that key among declared members **and** at least `tag_min_member_share` of declared members carry it. If exactly one key qualifies, use it; if none or more than one, skip the method and add a note (`tag_ambiguous` or `tag_unavailable`). Match case-insensitively to the tag keys seen in cost data, and use the cost data's exact spelling. Amount per month is the sum of matching facts across the workload's accounts. Keep the series (all complete months) for trend. Note: tag spend includes every resource with that tag, including resources outside the workload map. Say so in the notes.

**Method C: sole-workload account** (only when no A or B result). Applies when all members of the workload are in exactly one account, no other workload has members there, and the account's unplaced share (resources in residual groups, unmapped, and shared, divided by all resources the store holds for that account) is at most `sole_workload_max_unplaced_ratio`. Amount is the account total for the latest complete month. Mark `confidence = low` and note: "assumes the whole account belongs to this workload".

**Primary selection** (F-D5): tag, then resource-level, then sole-workload, then none. When two or more methods are available, keep **all** as entries, mark one `primary`, and if two are comparable (same unit, both nonzero) and differ by more than `attribution_disagreement_ratio` (relative to the larger), raise an `attribution_disagreement` data-quality finding. Note that windows differ (a monthly tag figure and a 13-day resource-level figure are not directly comparable): compare only after scaling both to a daily average over their window, and state that in the finding.

**Confidence**: `min_confidence(workload.confidence, method_confidence)`, where `method_confidence` comes from the ruleset. For method A, if `explained_ratio` is `None` or below `attribution_low_explained_ratio`, cap at `low`.

**No attribution possible**: the entry has `method = "none"`, `amount = None`, and a note. The workload still receives savings findings.

### 10.7 Spend change and drivers (`findings/drivers.py`)
For each linked account and for the aggregate of all accounts: take the latest complete month `M1` and the prior complete month `M0` (periods from `complete_months`). Use only facts with `estimated == False` for these months.
- If `M0` total is zero or either month is missing: emit no change or driver findings, and a `insufficient_history` data-quality finding.
- `spend_change` finding: prior, current, `delta = current - prior`, `pct = safe_ratio(delta, prior)`.
- Per service: `delta_s = current_s - prior_s` (missing is zero). `contribution_share = safe_ratio(delta_s, total_delta)` (`None` when `total_delta` is zero). Flags: `new_service` (prior zero, current positive), `ended_service` (current zero, prior positive). `pct_s = safe_ratio(delta_s, prior_s)` (`None` for new services).
- Select drivers: sort by absolute delta descending (ties: service name ascending), keep those with `abs(delta_s) >= driver_min_abs`, take at most `driver_top_n`. Increases and decreases are both drivers.
- The current partial month appears **only** as a separate `spend_change` value labelled `estimated` and is never compared with a complete month.
- These findings are account-level (no workload) with confidence `high`; `medium` if any contributing fact was estimated.
- Workload-level: when method B has at least two complete months, emit one `workload_spend` finding with the series and the latest month-over-month change (total only, no drivers; see F-D9).

### 10.8 Savings (`findings/savings.py`)
For each `Recommendation`, by `placement(account, resource_arn or resource_id)`:
- `action_type` in `commitment_action_types` → `commitment_opportunity`, **account-level**, never included in any workload total, regardless of placement.
- Otherwise `savings_opportunity`:
  - Placed in a workload → attached to it; confidence is the workload's confidence if the member is `declared`, else `min_confidence(workload confidence, medium)`.
  - Placed `Shared` → account-level, flag `shared_infrastructure`.
  - `Unplaced` → account-level, flag `resource_not_in_workload`.
  - `Unknown` or `Ambiguous` → account-level, flag `resource_not_in_graph` or `resource_id_ambiguous`.
  - Account-level confidence is `high` (observed directly, account-scoped).
- **Tier**: `quick_win` when `implementation_effort` is in `quick_win_efforts` **and** `restart_needed is False`; otherwise `plan_first` (including when `restart_needed` is `None`; be conservative). Flag `no_rollback` when `rollback_possible is False`. Flag `stale` when `last_refresh` is older than `stale_recommendation_days` before `as_of`.
- **Totals**: per workload, the sum of its non-commitment recommendation savings, labelled `sum_of_resource_recommendations` (not deduplicated). Per account, the **authoritative** figure is the hub's deduplicated total from the summaries. Compute the sum of all items for that account; if `sum_items - deduped > savings_dedupe_tolerance`, emit a `savings_overlap` data-quality finding containing both numbers and the text rule "do not add these figures". If summaries are missing for an account, record a note and use no authoritative total (never substitute the item sum silently).
- **Percent of workload spend**: savings divided by the workload's primary monthly spend, only when the primary is a monthly method and units match; otherwise `None`.
- A recommendation with a currency different from the Cost Explorer unit is excluded from percent calculations and raises `currency_mismatch`.

### 10.9 Data-quality findings (`findings/data_quality.py`)
Each carries evidence of the response or error that revealed it. Emit, with these rule IDs: `source_unavailable` (status `denied`, `not_enabled`, `partial`, `error` for each source and account), `resource_level_not_enabled`, `tag_cost_not_visible` (a workload-declaring tag key has no cost data: say "inactive or unused", because the two cannot be told apart), `attribution_low_explained_ratio`, `attribution_disagreement`, `unplaced_share_high`, `savings_overlap`, `stale_recommendations` (one per account with a count), `currency_mismatch`, `insufficient_history`.

### 10.10 Asks and talking points (`findings/asks.py`, template-based, no LLM)
Fixed templates with IDs, rendered with `format_money` and `format_pct`. Each ask lists the finding IDs that triggered it.

| Ask ID | Trigger | Template (placeholders in braces) |
|---|---|---|
| `A-RESOURCE-LEVEL` | `resource_level_not_enabled` or low explained ratio | "Enable resource-level data in Cost Explorer for {services} so spend can be assigned to workloads." |
| `A-TAG-ACTIVATE` | `tag_cost_not_visible` | "Activate the tag key '{key}' as a cost allocation tag, and apply it to untagged resources." |
| `A-COH-OPTIN` | `source_unavailable` for Cost Optimization Hub with `not_enabled` | "Opt in to Cost Optimization Hub for the accounts in scope." |
| `A-ACCESS` | `source_unavailable` with `denied` | "Grant the read-only role access to {source}." |
| `A-CONFIRM-WORKLOADS` | any workload with `low` confidence or ambiguous resources | "Confirm the boundaries of these workloads: {workloads}." |

Talking points (templates `T-DRIVER`, `T-QUICKWIN`, `T-GAP`, `T-CONFIRM`) cite the top finding of the relevant kind. A talking point is created only when its source finding exists.

### 10.11 Narrative and guardrail (`report/narrative.py`, `report/guardrail.py`)
- `Narrator` protocol: `narrate(core: ReportCore) -> Narrative`, where `ReportCore` is the report **without** narrative, and `Narrative` is a list of sections, each a list of sentences `{text, finding_ids}`.
- `TemplateNarrator` (real): fixed sentence templates per finding kind, producing sections `summary`, `account_spend`, `workloads`, `savings`, `data_coverage`, `asks`. Deterministic and always passes the guardrail (test this).
- `FakeNarrator` (tests): returns canned narratives, including deliberately bad ones.
- `BedrockNarrator`: stub, raises `NotImplementedError("SKELETON: LLM narrative step not approved")`. No SDK import at module level.
- **Guardrail** `check_narrative(narrative, core) -> GuardrailResult` with violation codes:
  - `G1_NO_CITATION`: a sentence has no finding IDs.
  - `G2_UNKNOWN_FINDING`: a cited ID is not in the report.
  - `G3_NUMBER_MISMATCH`: a money amount (regex for a currency code, a space, digits with separators and two decimals) or a percentage in the text is not among the cited findings' `rendered_values()`. Dates and finding IDs are exempt. Plain integers of two or more digits must equal an integer value in a cited finding.
  - `G4_CAUSAL_CLAIM`: the sentence contains a causal marker (`because`, `due to`, `caused by`, `as a result of`, `thanks to`, `driven by` is allowed only if every cited finding is a `spend_driver`, since "driven by" there is a ranking statement about contribution, not a cause; document this exception) and some cited finding has `supports_causal_claim == False`.
  - `G5_UNKNOWN_WORKLOAD`: a workload name appears that is not in the report.
  - `G6_TOO_LONG`: a sentence over 400 characters.
- **Fallback rule** (in `assemble.py`): run the guardrail per section. Any section with violations is **replaced** with the `TemplateNarrator` text for that section, and a note is added to `notes` listing section and violation codes. Never silently drop or silently keep a failing section.

### 10.12 Report assembly (`report/assemble.py`, `findings/build.py`)
1. Gather findings (drivers, savings, data quality) and attribution.
2. Sort findings: **sections are in a fixed order** (spend change, spend drivers, workload spend, savings quick wins, savings plan-first, commitments, data quality). Within a section sort by `abs(amount)` descending (the finding's primary monetary value; findings without one sort last), then confidence rank descending, then `finding_id` ascending (F-D12). Assign `display_id` after sorting.
3. Build `summary` (each figure with its finding IDs), `buckets`, per-workload sections, `account_level`, `customer_asks`, `talking_points`, `coverage`, `evidence_index`.
4. Run the narrator, then the guardrail with fallback.
5. Serialise (sorted keys, Decimals as strings, trailing newline). Compute `build_result_sha256` over the BuildResult JSON bytes.

---

## 11. Build steps (each: tests first, then code, then `make check`)

Follow the TDD protocol of the existing project: list tests, write them, **watch them fail for the right reason**, write the minimum code, run the whole suite, refactor while green, update `docs/STATUS.md`, then move on. Never weaken a test to pass it. Never tune a default to satisfy a test without recording the reason in `docs/DECISIONS.md`.

### F1 Money and periods
**Tests first:** `parse_amount` accepts `"12.30"`, `12`, `Decimal("1.5")`; converts `0.1` through `str` (assert `Decimal("0.1")`, not `0.1000000000000000055`); rejects `NaN`, `Infinity`, `True`, `None`. `quantize_cents` rounds `2.675` to `2.68` (ROUND_HALF_UP). `format_money` for `Decimal("6900")` is `"USD 6,900.00"`; negative is `"-USD 120.00"`; zero is `"USD 0.00"`. `format_pct(Decimal("0.092"))` is `"9.2%"`; `None` is `"n/a"`. `safe_ratio` with zero denominator is `None`. `Period` rejects `end <= start`. `complete_months(date(2026,11,19), 3)` is Aug, Sep, Oct; across a year boundary (`as_of = 2027-01-05`, `n=3`) is Oct, Nov, Dec 2026; leap-year February handled; `current_partial_month(date(2026,11,1))` is `None`; `trailing_window` bounds. A test greps `src/workload_graph/{cost,findings,report}` for `float` and wall-clock calls.

### F2 Models and ruleset
**Tests first:** every model round-trips through JSON; Decimals serialise as strings and parse back exactly; frozen models reject mutation; `Finding` requires evidence (except the documented data-quality case); `finding_id` is stable across runs and changes when the subject changes; `FindingsRuleset` defaults validate, reject unknown keys and out-of-range ratios, produce a stable hash, and the hash changes when any field changes and does not change with key order.

### F3 Snapshot extension and pointer resolver
**Tests first:** new sources are accepted by the existing loader; cost snapshots load with `Decimal` (a stored `12.30` is `Decimal("12.30")`, never a float); existing sources still load exactly as before (regression test); `resolve_pointer(snapshot, pointer)` returns the datum for valid pointers and raises a clear error for invalid ones.

### F4 Cost Explorer normaliser
**Tests first:** monthly service-by-account groups become facts with correct dimensions taken from `GroupDefinitions`; a response with the group definitions in the opposite order (account first) still maps correctly; tag keys split on the first `$` (`"app$orders"` gives key `app`, value `orders`; `"app$"` gives untagged; `"app$a$b"` gives value `a$b`); `Estimated` is carried; amounts are `Decimal`; daily window calls produce correct per-account window totals; a missing metric skips the group with a note; evidence pointers resolve to the exact group (use `resolve_pointer`); output is sorted and unchanged when input pages are reordered.

### F5 Cost Optimization Hub normaliser
**Tests first:** a full item maps every field; optional fields absent become `None` (assert `None`, not zero or empty string); float inputs from stubbed responses convert via `str`; a recommendation without ARN or resource ID is kept; summaries yield `(account, deduped_total, currency)`; a summary without the deduplicated total produces a note and no record; pointers resolve.

### F6 Cost Explorer collector (Stubber)
**Tests first:** each labelled call is made with the expected parameters (assert the exact request, including exclusive end dates and `GroupBy`); pagination through `NextPageToken` across at least three pages; `GetTags` result drives which tag-key calls are made; resource-level calls are made only for the top-N services from call 1 and are skipped (with a note) when call 1 fails; access denied is recorded as `denied` per label and other labels continue; an error matching the `not_enabled` matcher is recorded as `not_enabled`; throttling retries with an injected no-op `sleep` and counts against `max_calls`; exceeding `max_calls` returns `partial`; payer mode (`access != linked`) and per-account mode both build correct filters. **Allowlist test:** wrap the client; any operation outside the allowlist, or starting with a forbidden prefix, fails.

### F7 Cost Optimization Hub collector (Stubber)
**Tests first:** same pattern as F6, plus: the collector checks the service model for `ListEnrollmentStatuses` and skips that call with a note if absent (test with a model lacking it); `includeAllRecommendations` is `False`; `nextToken` pagination; one summary call per linked account; a not-opted-in error maps to `not_enabled`; allowlist test explicitly asserts that `UpdateEnrollmentStatus` and `UpdatePreferences` are refused.

### F8 Access plan, scope and snapshot cache
**Tests first:** `AccessPlan` validates 12-digit IDs and a non-empty linked list; the cache key is stable and changes with any parameter; a hit within 24 hours (injected clock) returns the stored snapshot without calling the collector; a miss after the TTL calls it; the cache never stores a snapshot with status `error`; a corrupt cache file is ignored with a note.

### F9 Resource index and placement
**Tests first:** ARN and physical-id matching (`i-0abc`, S3 bucket name, Lambda function name); two resources sharing a physical id in one account become ambiguous and never match; the same physical id in two different accounts does not collide; placement results for a workload member (declared and attached), shared infrastructure, residual, unmapped, unknown, ambiguous.

### F10 Attribution
**Tests first (hand-computed numbers, assert exact Decimals):** method A buckets for a small account sum to the window total exactly; `NoResourceId` rows go to `no_resource_id`; resources outside the graph go to `unknown`; shared infrastructure goes to `shared`; `unexplained` equals total minus explicit rows; `explained_ratio` values; a workload spanning two accounts sums both; method B resolves a unique tag pair, skips on two qualifying keys (`tag_ambiguous`), skips when the member share is under the threshold, and matches tag-key case-insensitively; method C only when the account has one workload and the unplaced share is within the limit; primary selection order (tag over resource-level over sole-account over none); the disagreement finding fires only above the ratio and compares daily averages; confidence follows `min` and the low-explained cap; a workload with no method has `amount None`.

### F11 Drivers
**Tests first:** a two-month example with exact deltas, shares and percentages; `new_service` and `ended_service` flags with `pct None`; zero total delta gives `contribution_share None`; `driver_min_abs` and `driver_top_n` limits; ties broken by service name; estimated months are excluded; zero prior total or missing month yields `insufficient_history` and no drivers; currency mismatch raises and becomes a data-quality finding; the aggregate scope equals the sum of accounts; the partial current month never enters a comparison.

### F12 Savings
**Tests first:** commitment recommendations are account-level and absent from workload sums; tiering (quick win requires low effort **and** `restart_needed is False`; `None` is plan-first); `no_rollback` and `stale` flags (use an injected `as_of`); each placement outcome; declared versus attached confidence; the overlap finding fires only when item sum exceeds the deduplicated total by more than the tolerance and carries both numbers; missing summaries produce a note and no authoritative total; percent of workload spend only with a monthly primary and matching units.

### F13 Data-quality findings and confidence
**Tests first:** one test per rule ID in Section 10.9 with the evidence pointing at the revealing response or error; `tag_cost_not_visible` wording says "inactive or unused"; `min_confidence` truth table; stale-recommendation counts per account.

### F14 Asks and talking points
**Tests first:** each trigger yields exactly its ask; asks list the triggering finding IDs; templates render with the shared formatters; a talking point appears only when its source finding exists; ordering is deterministic.

### F15 Narrative and guardrail
**Tests first (one per violation code, plus the happy path):** `TemplateNarrator` output passes the guardrail for every scenario; `FakeNarrator` outputs with a missing citation (`G1`), an unknown ID (`G2`), a wrong or reformatted number (`G3`; also assert `"USD 6900"` without separators fails), causal language (`G4`; and the "driven by" exception for a `spend_driver` only), an unknown workload name (`G5`), and an over-long sentence (`G6`) are each rejected with the right code; the fallback replaces only the failing section and records a note; `BedrockNarrator` raises `NotImplementedError` and imports no SDK at import time.

### F16 Assembly, determinism and invariants
**Tests first (most important):**
- Shuffling facts, recommendations, workloads and snapshot order gives an **identical** serialised report (use `hypothesis`).
- Running in subprocesses with at least three different `PYTHONHASHSEED` values gives identical output (compare SHA-256).
- **Reconciliation invariant:** for every account and window, the sum of buckets equals the account total exactly (property test over generated facts).
- **Citation invariant:** every `finding_id` in sections and narrative exists; every finding has at least one evidence record; every evidence pointer resolves.
- **No-float invariant:** the serialised report contains no JSON number for any monetary field.
- **Fixed section order** and `display_id` assignment follow the sort rules.
- `build_result_sha256` changes when the BuildResult changes; the report is unchanged by cosmetic reordering of the BuildResult JSON.

### F17 Pipeline and CLI
`pipeline_findings.run(build_result_path, snapshots_dir, findings_ruleset, as_of, narrator)` reads snapshots (`<access-account-id>/cost_explorer.json` and `.../cost_optimization_hub.json`, following the existing loader's naming), normalises, builds findings, assembles the report, and writes JSON.
CLI: `workload-graph findings --build-result FILE --snapshots DIR --as-of YYYY-MM-DD --findings-ruleset FILE --out FILE`. Exit codes: `0` success, `2` bad input. A missing source is a coverage note, not a crash.
**Live collection is not wired** (`NotImplementedError("SKELETON: live wiring requires approved roles")`), like the existing `collect`.
**Tests first:** the command over each scenario directory writes valid JSON; repeat runs are byte-identical; a missing cost snapshot produces coverage `error` and a note; a missing BuildResult exits `2`; no wall-clock calls (the date comes only from `--as-of`).

### F18 (optional, after F1 to F17 are green): forecast
Add `GetCostForecast` (current month remainder) to the Cost Explorer collector, normaliser, and a `spend_forecast` finding marked estimated. Same TDD discipline. Skip if time is short; record in `docs/STATUS.md`.

### F19 (optional, after F18): commitment coverage and utilisation
Add Savings Plans and Reserved Instance coverage and utilisation calls and findings (low coverage means unused discount potential; low utilisation means paying for unused commitment). Thresholds are provisional ruleset fields. Note: resource-level data does not apply to these reports.

---

## 12. Scenarios (build programmatic fixtures in `tests/fixtures/builders.py`, plus frozen JSON for the starred ones)

Describe each in `docs/COST_SCENARIOS.md`. All values fake.

| # | Scenario | Expected |
|---|---|---|
| C1* | Tagged workload, tag cost active, recommendations match by ARN | Full workload section; tag method primary; savings attached |
| C2* | Resource-level data available for top services | Buckets reconcile exactly; method A entry with explained ratio |
| C3 | No tags, no resource-level, two workloads in one account | Spend `none`; account total fully `unplaced`/`unexplained`; savings still attach by ARN |
| C4 | Sole workload in an account | Method C, confidence `low`, note present |
| C5* | Cost Optimization Hub not opted in | `source_unavailable` (`not_enabled`) plus `A-COH-OPTIN`; no savings findings |
| C6 | Cost Explorer denied | Spend sections absent with coverage `denied` plus `A-ACCESS`; savings still present |
| C7 | Item sum exceeds deduplicated total | `savings_overlap` with both numbers |
| C8 | Commitment purchase recommendations | Account-level only, excluded from workload sums |
| C9 | New service appears (prior month zero) | Driver with `new_service`, `pct None` |
| C10 | Current month partial and estimated | Never compared with complete months; shown as estimated |
| C11 | Load balancer in shared infrastructure with spend | `shared` bucket; no allocation |
| C12 | Recommendation on a resource not in the graph | Account-level finding flagged `resource_not_in_graph` |
| C13 | Payer plan covering three accounts | Findings per account and aggregate are correct |
| C14 | Currency mismatch | Not summed; `currency_mismatch` finding |
| C15 | Stale recommendations | One `stale_recommendations` finding per account with a count |
| C16 | Members carry two different tag values | Tag method skipped (`tag_ambiguous`) |
| C17 | Multi-page Cost Explorer responses | All pages merged; totals correct |
| C18 | Tag and resource-level disagree by more than the ratio | `attribution_disagreement` computed on daily averages |

---

## 13. Documentation to produce or update

- `docs/FINDINGS.md`: the reasoning chain (Section 1, condensed), the pipeline diagram in text, finding kinds, the attribution ladder, the guardrail rules, and how to read the report.
- `docs/IAM_PERMISSIONS.md`: the minimal read-only permissions needed (`ce:GetCostAndUsage`, `ce:GetCostAndUsageWithResources`, `ce:GetTags`, `ce:GetCostForecast` only if F18 is built; `cost-optimization-hub:ListRecommendations`, `ListRecommendationSummaries`, `ListEnrollmentStatuses`, `GetRecommendation`, `GetPreferences`). Mark the exact action names `# ASSUMPTION:` to verify. Documentation only; do not deploy anything.
- `docs/COST_SCENARIOS.md`, and updates to `docs/DECISIONS.md` (Section 2), `docs/ASSUMPTIONS.md` (Section 3 unverified list), `docs/OPEN_QUESTIONS.md`, `docs/STATUS.md`.
- Seed `docs/OPEN_QUESTIONS.md` with: who owns the management account (payer or per-account mode); whether customers will activate a workload tag; whether amortised cost should be the default metric; how shared infrastructure cost should be allocated; whether customers' Support plans allow adding Trusted Advisor next; what the TAM-facing review format should be; acceptable freshness for cached cost data.

---

## 14. Definition of done and final report

- [ ] Baseline `make check` green before starting; green after every step.
- [ ] All steps F1 to F17 done in order, each started with failing tests (F18, F19 optional and recorded).
- [ ] Existing tests still pass unchanged; only the edits in F0.3 were made to existing files.
- [ ] Read-only allowlist tests pass for both collectors, including the explicit refusal of `UpdateEnrollmentStatus` and `UpdatePreferences`.
- [ ] Determinism tests pass, including the multi-`PYTHONHASHSEED` test.
- [ ] Reconciliation, citation and no-float invariants pass.
- [ ] Scenarios C1 to C18 pass.
- [ ] No `float` for money, no wall-clock calls, no credentials, no real IDs, no forbidden plural from rule 9, in `src/`, `tests/` and `docs/`.
- [ ] Docs in Section 13 exist and are seeded.
- [ ] Final message lists: what was built; what is real versus `SKELETON`; test counts (passed, skipped, failed) and coverage; every decision you made yourself; every `# ASSUMPTION:` you relied on; the five biggest open questions; and the first three things you recommend doing once real cost data is available.

---

## 15. Guidance for later changes

1. Read `docs/STATUS.md`, `docs/OPEN_QUESTIONS.md`, `docs/ASSUMPTIONS.md` and `docs/DECISIONS.md` first.
2. Treat anything marked `SKELETON`, `ASSUMPTION` or `DECISION-PENDING` as provisional.
3. When an assumption is verified or corrected, change the single isolated function, update the pinning test, and move the entry in `docs/ASSUMPTIONS.md`.
4. Add real anonymised cases as new scenarios **before** changing rules.
5. Add a new data source (Trusted Advisor, Support, Health) with the same shape: collector with allowlist, pure normaliser, findings with evidence pointers, fixtures, scenarios, guardrail coverage. Do not add it until the cost path is validated.
6. When a finding type gains genuine causal evidence, set `supports_causal_claim` for that type only, add a guardrail test, and record the decision.
7. When something is ambiguous, ask the user. Do not guess.
