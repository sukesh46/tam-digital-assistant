# CURSOR INSTRUCTIONS: Workload Graph Construction (test-driven build)

Read this whole file before creating or editing anything. Re-read the relevant section before each build step.

---

## 0. Purpose, scope and hard rules

### 0.1 What you are building
A Python library plus a small CLI that **reconstructs candidate workloads from poorly organised AWS accounts**. Given read-only snapshots of what exists in a customer's AWS accounts, it produces **candidate workloads**: groups of resources that appear to belong together, each with a confidence level and the evidence behind every grouping.

It is one component of a larger "TAM Digital Copilot". Downstream components (findings, health scores, QBR drafts) will consume its output. Those components are **not** part of this task.

### 0.2 Why it exists
Customer environments are often untagged or inconsistently organised. Existing AWS tools (tag-based grouping, AppRegistry, Well-Architected workloads) assume someone already organised the account. A TAM needs per-workload views to be useful, so this component proposes workloads from weak and strong signals. **A human confirms the result.** The output is a proposal, never a verdict.

### 0.3 In scope
- Normalised data model for resources, edges and evidence.
- Four deliberately limited sources: **tags, CloudFormation stacks, network topology (EC2/ELB describe calls), CloudTrail management events (seeded)**.
- Collectors (read-only, tested offline), a session provider for assuming read-only roles, a capability probe.
- A deterministic graph builder, cross-account handling, evaluation metrics, a golden-scenario test suite, and a CLI that runs offline over stored snapshots.

### 0.4 Out of scope (do NOT build)
LLM naming or explanation, web UI or API, QBR or health logic, cost data, AWS Config, VPC Flow Logs, AppRegistry, Terraform or CDK state, CMDB or meeting notes, scheduling, deployment infrastructure, anything that writes to AWS.
If you are tempted to add one of these, stop and add a line to `docs/OPEN_QUESTIONS.md` instead.

### 0.5 Hard rules (never break)
1. **Read-only toward AWS.** No write, create, update or delete API calls anywhere. Enforce this with a test (Step 5).
2. **No live AWS or network calls**, in code run by tests or in tests. All tests must pass offline. Add an autouse fixture that fails any socket connection attempt.
3. **No credentials, real account IDs, ARNs or customer data** anywhere. Use obviously fake values (`111111111111`, `222222222222`, `example-customer`). Never log credentials or full CloudTrail payloads.
4. **Deterministic output.** Same input and same ruleset must give byte-identical output, regardless of input order or `PYTHONHASHSEED`.
5. **No invented facts.** If you are not sure how an AWS API behaves, write `# ASSUMPTION:` in code, add it to `docs/ASSUMPTIONS.md`, and keep going. Never silently guess.
6. **Ambiguity goes to `docs/OPEN_QUESTIONS.md`.** Pick the safest, most reversible option, record it, and mention it in your final report. Do not stop to ask unless a hard rule would be broken.
7. Use the word **"customers"**. Do not use the word "clients" in code, comments, docs or identifiers.
8. Do not create, modify or delete anything outside the project directory. Do not install anything globally. Do not commit or push unless the user asks.
9. Every placeholder or provisional choice must carry a marker (Section 10.4).

---

## 1. The reasoning behind this design (read this; it explains the decisions)

Follow this chain; each step motivates the next.

1. **The problem.** TAMs cannot review every workload by hand, and customer environments are messy. We want per-workload coverage across a whole portfolio.
2. **"Workload" is a business concept.** Shared services and common networking belong to several workloads at once, and two people can draw boundaries differently. So there is no ground truth the code can converge on alone. Therefore the output is **candidate workloads with evidence and confidence, to be confirmed by a human**.
3. **Many signals exist, but each costs development effort and risk.** So we start with a deliberately **small set of deterministic, low-volume sources** and add more only when evaluation shows a specific gap.
4. **Log-heavy sources are expensive and hard to bound.** CloudTrail and VPC Flow Logs are huge. Random sampling is biased. So logs are a **second layer, seeded by identifiers we already know**: a query is a join against a small set of known resources, so cost scales with the seeds, not with log volume. We skip Flow Logs entirely in this version.
5. **Not everything is enabled in customer accounts.** Config, AppRegistry and Flow Logs are opt-in. So the system must **probe what is available** and report which sources contributed to each result. A workload built from three sources is more trustworthy than one built from one.
6. **Declared beats inferred.** Stack membership and workload tags are *declarations*. Subnet and VPC co-location are *hints*. The graph builder therefore uses declared groupings as **seeds**, attaches resources using weaker evidence only above a threshold, and labels the rest as low-confidence or unmapped.
7. **Shared infrastructure must not merge workloads.** One load balancer or VPC can serve many workloads. If we merged along every strong edge, two workloads would collapse into one. So only **grouping declarations** merge seeds; other edges can only *attach* a resource to a cluster or mark infrastructure as *shared*.
8. **Explainability over cleverness.** Every grouping must trace back to evidence (source, time, raw pointer). We therefore use simple, auditable rules (seeds, bounded affinity scoring) instead of black-box clustering. Community-detection algorithms can be experimented with later against the golden scenarios.
9. **Cross-account references are metadata, not permission.** Seeing a reference to another account never causes access to it. The reference is stored as an edge with an unresolved end, which resolves only if that account is in the approved scope, otherwise it becomes an explicit external stub and a finding.
10. **Separate input/output from logic.** Collectors do I/O and return raw snapshots. Normalisers and the graph builder are **pure functions**. This is what makes the system testable offline and reproducible.
11. **Test-driven development with fixtures built from real API shapes.** `botocore.stub.Stubber` validates stubbed responses against the service models that ship inside botocore, so fixtures cannot drift from real response shapes. We prefer it over a mocking library because it is exact, deterministic, and has no extra dependency.
12. **Prove it on messy cases first.** The golden scenarios (Section 9) include untagged, ambiguous and cross-account cases. Evaluation metrics let us measure whether simple rules are enough before adding sources.

---

## 2. Decisions and their reasons

| # | Decision | Reason | Revisit when |
|---|---|---|---|
| D1 | Python 3.11 only (`>=3.11,<3.12`) | Required by the user; keeps the toolchain consistent | User changes the target |
| D2 | `venv` + `pip` + `pyproject.toml`, `src` layout | Simplest portable setup; the `src` layout prevents accidental imports from the working directory | Packaging needs grow |
| D3 | `pydantic` v2 for models | Validation at the boundaries, JSON round-trip, immutability | Performance problems |
| D4 | `networkx` for the in-memory graph only | Tested graph structure and neighbour queries; clustering logic stays ours | Clustering experiments |
| D5 | Own seed-and-attach algorithm, no ML or community detection initially | Explainable, deterministic, easy to test | Golden metrics show a gap |
| D6 | `botocore.stub.Stubber` for AWS tests; no `moto` | Exact service-model validation, no extra dependency, no hidden behaviour | Moto coverage becomes necessary |
| D7 | `hypothesis` for property tests | Invariants (determinism, partition) are best tested with generated inputs | None |
| D8 | Raw snapshots stored as JSON and replayable | Lets the builder run offline on stored data and makes failures reproducible | None |
| D9 | Four sources only (tags, stacks, topology, seeded CloudTrail) | Deterministic, low volume, mostly default-available | Evaluation shows gaps |
| D10 | Canonical key = (account_id, region, arn) | ARNs alone are not unique for some resources (such as S3 buckets) and account is needed for cross-account joins | None |
| D11 | Bounded affinity score (Section 7) | One contribution per edge type per cluster, so many weak edges cannot pile up | Tuning with golden data |
| D12 | Ruleset is versioned, hashed and in the output | Any result can be reproduced and compared across rule changes | None |
| D13 | `partition = "aws"` only | China and GovCloud partitions are out of scope until requested | A customer needs them |
| D14 | Parquet store is optional (extra) | Avoids a heavy dependency in the core path | Athena integration |

Record each of these in `docs/DECISIONS.md` with status `provisional` and add new ones as you make them.

---

## 3. Environment setup (do this first; run these exactly)

1. Confirm Python 3.11 is available: `python3.11 --version`. **If it is not, stop and tell the user. Do not fall back to another version.**
2. Create the project directory `workload-graph/` and work inside it.
3. Create `.python-version` containing `3.11`.
4. Create the venv **inside the project**: `python3.11 -m venv .venv`
5. Activate it: `source .venv/bin/activate` (Windows: `.venv\Scripts\activate`).
6. Upgrade pip inside the venv only: `python -m pip install --upgrade pip`
7. Create `requirements/base.txt`:
   ```
   # SKELETON: ranges, not pins. Lockfile strategy undecided (see docs/DECISIONS.md).
   boto3>=1.34,<2          # AWS SDK; collectors only, behind a client factory
   pydantic>=2.6,<3        # domain models and validation
   networkx>=3.2,<4        # in-memory graph structure and neighbour queries
   ```
8. Create `requirements/dev.txt`:
   ```
   -r base.txt
   pytest>=8.0
   pytest-cov>=5.0
   hypothesis>=6.100
   ruff>=0.4
   mypy>=1.9
   boto3-stubs[ec2,elbv2,cloudformation,cloudtrail,resourcegroupstaggingapi,sts]>=1.34
   types-networkx             # if unavailable, set ignore_missing_imports for networkx in mypy config
   ```
9. Create `requirements/parquet.txt` containing `pyarrow>=15` (optional extra, Step 14 only).
10. Install: `pip install -r requirements/dev.txt && pip install -e .`
11. Verify the toolchain: `python -c "import sys; print(sys.version)"`, `pytest --version`, `ruff --version`, `mypy --version`. Record the versions installed in `docs/STATUS.md`.
12. Never run `pip install` outside the venv. Never use `sudo`.

### 3.1 pyproject.toml
- `[project]`: `name = "workload-graph"`, `version = "0.0.0"`, `requires-python = ">=3.11,<3.12"`, dependencies mirroring `requirements/base.txt`, console script `workload-graph = "workload_graph.cli:main"`.
- `[tool.setuptools.packages.find]` with `where = ["src"]`.
- `[tool.pytest.ini_options]`: `testpaths = ["tests"]`, `addopts = "-ra --strict-markers --cov=workload_graph --cov-report=term-missing"`, markers `determinism`, `scenario`, `slow`.
- `[tool.ruff]`: line length 100, select `E,F,I,B,UP,N,S`; per-file ignore `S101` in `tests/`.
- `[tool.mypy]`: `strict = true`, `files = ["src"]`, `plugins = ["pydantic.mypy"]`.

### 3.2 Makefile targets (each with a one-line comment)
`install`, `lint` (`ruff check .`), `format` (`ruff format .`), `typecheck` (`mypy src`), `test` (`pytest -q`), `test-unit`, `test-scenarios`, `test-determinism`, `check` (lint + typecheck + test).

### 3.3 .gitignore
`.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.hypothesis/`, `.coverage`, `*.egg-info/`, `dist/`, `build/`, `out/`.

---

## 4. Repository layout (create exactly this)

```
workload-graph/
├── README.md
├── CURSOR_INSTRUCTIONS.md                  # this file, verbatim
├── pyproject.toml  Makefile  .gitignore  .python-version
├── requirements/{base.txt, dev.txt, parquet.txt}
├── docs/
│   ├── DECISIONS.md  ASSUMPTIONS.md  OPEN_QUESTIONS.md
│   ├── EDGE_TYPES.md  SCENARIOS.md  STATUS.md
├── src/workload_graph/
│   ├── __init__.py
│   ├── ids.py                      # ResourceKey, arn_for(), account validation
│   ├── models/
│   │   ├── __init__.py
│   │   ├── evidence.py             # Evidence
│   │   ├── resource.py             # Resource, ExternalRef
│   │   ├── edge.py                 # EdgeType, Edge
│   │   ├── snapshot.py             # RawSnapshot, SourceName, SnapshotStatus
│   │   ├── probe.py                # ProbeResult, SourceAvailability
│   │   └── workload.py             # CandidateWorkload, ResidualGroup, SharedInfra, BuildResult
│   ├── ruleset.py                  # Ruleset (versioned, hashed)
│   ├── auth/
│   │   ├── __init__.py
│   │   └── session.py              # SessionProvider protocol, StsSessionProvider
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── base.py                 # Collector protocol, retry/budget helpers
│   │   ├── tags.py  stacks.py  topology.py  cloudtrail.py
│   ├── normalisers/
│   │   ├── __init__.py
│   │   ├── base.py                 # NormalisedBatch
│   │   ├── tags.py  stacks.py  topology.py  cloudtrail.py
│   ├── probe.py                    # capability probe from snapshot statuses
│   ├── store/
│   │   ├── __init__.py
│   │   ├── base.py                 # GraphStore protocol
│   │   ├── memory.py               # InMemoryGraphStore (networkx)
│   │   └── parquet.py              # optional, Step 14
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── seeds.py                # declared grouping -> seed clusters
│   │   ├── attach.py               # affinity scoring and attachment
│   │   ├── shared.py               # shared infrastructure detection
│   │   ├── residual.py             # residual groups and unmapped
│   │   ├── crossaccount.py         # pending/resolved/external edges
│   │   ├── confidence.py           # confidence derivation
│   │   └── build.py                # build_workloads(): orchestrates the above
│   ├── evaluation/
│   │   ├── __init__.py
│   │   ├── metrics.py              # pairwise precision/recall/F1, coverage
│   │   └── golden.py               # load golden scenarios
│   ├── pipeline.py                 # probe -> normalise -> store -> build -> report
│   ├── cli.py                      # argparse CLI
│   └── logging_setup.py
└── tests/
    ├── conftest.py                 # network block, frozen clock, fixture loaders
    ├── unit/                       # one test_<module>.py per module above
    ├── scenarios/                  # test_scenarios.py, one case per golden scenario
    ├── determinism/                # test_determinism.py, test_invariants.py
    └── fixtures/
        ├── README.md               # fake data only; never real customer data
        ├── raw/<scenario>/<account>/<source>.json     # RawSnapshot files
        └── golden/<scenario>.json                     # expected workloads
```

Each `.py` file in `src/` starts with a module docstring: one-line summary, `Status:` (`stub`, `partial`, or `real`), purpose, inputs, outputs, planned dependencies, and notes. Full type hints and docstrings on every public function and class.

---

## 5. TDD protocol (mandatory for every step in Section 8)

For each module or behaviour:
1. **List the test cases** in the step (they are given below; add more where you see edge cases).
2. **Write the tests first.** Run them and **confirm they fail for the right reason** (an import error or a `NotImplementedError` is fine; an unrelated failure is not).
3. **Write the minimum code** that makes them pass. No extra features.
4. **Run the whole suite** (`make check`). Fix regressions before moving on.
5. **Refactor** only while tests are green. Re-run.
6. Update `docs/STATUS.md` (module, status, tests added, open issues).
7. Only then start the next step.

Rules:
- One behaviour per test; test names read as sentences (`test_stack_member_edges_are_created_for_every_stack_resource`).
- Tests use fake data only and are offline.
- Never loosen or delete a test to make it pass. If a test is wrong, fix it and record why in `docs/DECISIONS.md`.
- Never tune rule weights to satisfy a test without recording the reason in `docs/DECISIONS.md`.
- Keep coverage above 90% for `graph/`, `normalisers/`, `models/`, `ids.py`, `ruleset.py`.

---

## 6. Domain model specification

All models are immutable (`frozen=True`), hashable where used as keys, validated, JSON-serialisable, and use timezone-aware UTC datetimes.

### 6.1 Identity (`ids.py`)
- `AccountId`: exactly 12 digits (string). Reject anything else.
- `ResourceKey`: `(account_id, region, arn)`. `region` is `"global"` for global services. `key_str()` returns `"{account_id}|{region}|{arn}"`. Keys sort lexicographically by `key_str()`.
- `arn_for(resource_type, physical_id, account_id, region)`: builds an ARN for a **small, explicit table** of types (ec2 instance, subnet, vpc, security group; elbv2 load balancer and target group; others as needed). For types not in the table, return a deterministic placeholder key `unresolved:{resource_type}:{physical_id}` and record a note. `# ASSUMPTION:` partition `aws` only.

### 6.2 Evidence (`models/evidence.py`)
`Evidence`: `source` (one of `tags`, `stacks`, `topology`, `cloudtrail`), `observed_at`, `raw_ref` (snapshot id + JSON pointer so a human can find the original datum), `detail` (short string, no secrets, no full payloads).

### 6.3 Resource (`models/resource.py`)
- `Resource`: `key`, `resource_type`, `name | None`, `tags: dict[str, str]`, `attributes: dict[str, str]` (small, flat), `sources: frozenset[str]`.
- `ExternalRef`: `account_id`, `reason` (`out_of_scope`, `aws_owned`, `unknown`), optional `arn`. Represents an unresolved or out-of-scope endpoint.

### 6.4 Edge (`models/edge.py`)
- `EdgeType` enum: `STACK_MEMBER`, `TAG_WORKLOAD_KEY`, `LB_TARGET`, `SAME_SUBNET`, `SHARED_SECURITY_GROUP`, `SAME_VPC`, `CREATED_BY_SAME_PRINCIPAL`, `CROSS_ACCOUNT_REF`.
- `Edge`: `src: ResourceKey`, `dst: ResourceKey | ExternalRef`, `edge_type`, `evidence: tuple[Evidence, ...]` (**at least one**).
- Document each type in `docs/EDGE_TYPES.md`: what produces it, whether it is a grouping declaration, an attach hint, or informational, and how it can be wrong.

### 6.5 Raw snapshot (`models/snapshot.py`)
`RawSnapshot`: `snapshot_id`, `source`, `account_id`, `region`, `collected_at`, `operations_called: list[str]`, `payload: dict[str, list[dict]]` (operation name to list of response pages), `status` (`ok`, `empty`, `denied`, `partial`, `error`), `notes: list[str]`.

### 6.6 Probe (`models/probe.py`)
`ProbeResult`: for each account and source, a `SourceAvailability` (`available`, `empty`, `denied`, `partial`, `error`). Used in output so a TAM can see which sources contributed.

### 6.7 Output (`models/workload.py`)
- `CandidateWorkload`: `workload_id` (deterministic: first 12 hex chars of SHA-256 over the sorted member key strings of its **seed**), `name_hint` (deterministic heuristic, see Step 10; **not an LLM**), `members: list[Member]`, `confidence` (`high` | `medium` | `low`), `accounts: list[AccountId]` (a workload may span accounts), `sources_used: list[str]`, `shared_dependencies: list[ResourceKey]`, `external_refs: list[ExternalRef]`, `evidence_summary` (counts by edge type).
- `Member`: `key`, `membership` (`declared` or `attached`), `affinity: float | None` (for attached), `reasons: list[EdgeType]`.
- `SharedInfra`: `key`, `touches: list[workload_id]`.
- `ResidualGroup`: `(account_id, region, vpc_id)`, `members`, `confidence = "low"` always.
- `BuildResult`: `ruleset_version`, `ruleset_hash`, `workloads`, `shared_infrastructure`, `residual_groups`, `unmapped`, `external_refs`, `probe`, `notes`, `generated_from` (list of snapshot ids). Serialises with **sorted keys and stable ordering**.

---

## 7. Ruleset (`ruleset.py`) — all values PROVISIONAL

A pydantic model, serialisable to JSON, with a `version` string and a `content_hash()` computed over canonical JSON. The hash is placed in every `BuildResult`.

Fields and **provisional** defaults (chosen for relative ordering only, not calibration; mark each with `# SKELETON: provisional, tune with golden scenarios`):

| Field | Default | Meaning |
|---|---|---|
| `workload_tag_keys` | `["app", "application", "workload", "project", "service"]` | Tag keys treated as workload declarations. Provisional; customers vary. |
| `edge_weights` | `LB_TARGET 0.8`, `SAME_SUBNET 0.4`, `SHARED_SECURITY_GROUP 0.4`, `SAME_VPC 0.2`, `CREATED_BY_SAME_PRINCIPAL 0.3`, `CROSS_ACCOUNT_REF 0.0` | Strength of non-grouping evidence |
| `attach_threshold` | `0.6` | Minimum affinity to attach a resource to a cluster |
| `attach_margin` | `0.15` | Best cluster must beat the second best by this much, otherwise the resource is ambiguous |
| `confidence_high_declared_ratio` | `0.8` | Share of declared members for `high` |
| `confidence_medium_declared_ratio` | `0.5` | Share of declared members for `medium` |
| `shared_min_clusters` | `2` | A resource attached by strong evidence to this many clusters is shared infrastructure |
| `cloudtrail_window_minutes` | `30` | Window for "created alongside" |
| `cloudtrail_lookback_days` | `90` | Upper bound; event history limit is 90 days (`# ASSUMPTION:` verify) |
| `cloudtrail_max_calls_per_account` | `200` | Budget cap |
| `creation_event_names` | a short explicit list (for example `RunInstances`, `CreateBucket`, `CreateFunction`, `CreateLoadBalancer`, `CreateDBInstance`) | Provisional and incomplete by design |
| `known_aws_service_account_ids` | empty | `# TODO(input-needed):` fill from real data |

**Affinity** of a resource to a cluster = `1 − Π(1 − wᵢ)` over the **distinct edge types** linking that resource to any member of the cluster, taking the maximum weight per edge type. Reason: bounded in [0,1], independent of the number of members, order-independent, and explainable.

**Grouping declarations** (`STACK_MEMBER`, `TAG_WORKLOAD_KEY`) are not weighted. They form seeds (Step 10).

---

## 8. Build steps (each one: tests first, then code)

### Step 1 — Scaffolding and tooling
Create the layout, config files and venv (Sections 3 and 4). Empty modules with the required docstring.
**Tests first:** `test_package_imports`; `test_python_is_3_11` (asserts `sys.version_info[:2] == (3, 11)`); `test_network_access_is_blocked` (an autouse fixture patches `socket.socket.connect` to raise and the test confirms it does); `test_no_forbidden_word_in_source` (scans `src/`, `tests/` and `docs/` for the word "clients").
**Done when:** `make check` is green with these tests.

### Step 2 — Identity and models
**Tests first:** `AccountId` rejects non-12-digit input; `ResourceKey` equality and hashing; `key_str()` format and sort stability; `arn_for()` known types produce correct ARNs (assert exact strings), unknown types yield the `unresolved:` placeholder plus a note; `Evidence` requires timezone-aware datetimes; `Edge` rejects empty evidence; `ExternalRef` model; frozen models raise on mutation; JSON round-trip for every model; `BuildResult` serialises with sorted keys.
**Done when:** all models exist and pass type checking under strict mypy.

### Step 3 — Ruleset
**Tests first:** defaults validate; unknown edge-type keys are rejected; weights and thresholds are within [0,1]; the hash is stable across runs; changing any weight changes the hash; key order in input JSON does not change the hash; load and save round-trip.

### Step 4 — Normalisers (pure functions over `RawSnapshot`)
Each normaliser takes a `RawSnapshot` and returns a `NormalisedBatch` (`resources`, `edges`, `notes`). Build fixtures by hand under `tests/fixtures/raw/` shaped like real boto3 responses.
- **tags** (Resource Groups Tagging API `get_resources`): *Tests first:* resources with a configured workload tag key produce `TAG_WORKLOAD_KEY` edges between all resources sharing the value; non-workload tags are kept on the resource but produce no edge; untagged resources are still emitted as resources; one resource with two different values for workload tag keys produces a conflict note and **no** edges for it; tag keys are matched case-insensitively; empty pages are tolerated.
- **stacks** (`list_stacks`, `describe_stack_resources` or equivalent): *Tests first:* every resource in a live stack gets a `STACK_MEMBER` edge to its stack group; `DELETE_COMPLETE` stacks are ignored; nested stacks record a parent link and the child's resources belong to the child stack group; stack resources that return only physical IDs are converted via `arn_for()`; resources of unknown types still appear with the `unresolved:` key and a note.
- **topology** (EC2 and ELBv2 describe calls): *Tests first:* VPC and subnet membership produce `SAME_VPC` and `SAME_SUBNET` edges (pairwise edges between members of the same subnet are **not** generated; instead, emit edges from each resource to its subnet and let the builder reason over that — see note below); load balancer target groups produce `LB_TARGET` edges from the load balancer to resolvable targets; targets given by IP address are recorded as an unresolved note, not guessed; shared security groups produce `SHARED_SECURITY_GROUP` edges; VPC peering with a peer owner different from the requester emits `CROSS_ACCOUNT_REF`; Transit Gateway attachments with a different resource owner emit `CROSS_ACCOUNT_REF`.
  *Note:* subnets, VPCs and security groups are themselves resources. Edges go resource → container. The builder treats two resources as linked via a container when they share it. This avoids quadratic edge growth.
- **cloudtrail** (seeded `lookup_events`): *Tests first:* events for seed resources produce a creator record (principal ARN, time); two resources created by the same principal within `cloudtrail_window_minutes` produce `CREATED_BY_SAME_PRINCIPAL` edges; a creation event for a resource not in the seed set produces an **undeclared** `Resource` (flagged) so it can be attached later; events outside the lookback window are ignored; an event whose `userIdentity.accountId` differs from `recipientAccountId` produces a `CROSS_ACCOUNT_REF`; malformed events are skipped with a note, never an exception; no raw event payload is copied into evidence `detail`.

### Step 5 — Collectors (I/O, tested with `botocore.stub.Stubber`)
Each collector implements `collect(client_factory, account, region, scope) -> RawSnapshot`. `client_factory(service, account_id, region)` returns a boto3 client; tests return a Stubber-wrapped client.
**Tests first (per collector):** a single-page and a multi-page response are both handled and recorded; `AccessDenied` produces status `denied`, not an exception; throttling errors are retried with exponential backoff using an **injected** `sleep` function (tests pass a no-op) and eventually give status `partial` or `error` with a note; the call budget (`cloudtrail_max_calls_per_account`) is enforced and yields `partial`; `operations_called` lists every operation actually invoked.
**Read-only enforcement test (mandatory):** each collector declares `allowed_operations: frozenset[str]`; a test wraps the client so any call to an operation outside that set (or any operation whose name starts with `Create`, `Delete`, `Put`, `Update`, `Modify`, `Run`, `Start`, `Stop`, `Terminate`, `Attach`, `Detach`, `Associate`, `Disassociate`, `Tag`, `Untag`) fails the test.
**Seeded CloudTrail design:** Pass 1 calls `lookup_events` once per seed resource name or ID (the API accepts one lookup attribute per call). Pass 2 calls `lookup_events` by `EventName` for names in `creation_event_names` within windows around known creation times, keeping only events by principals seen in Pass 1. Both passes share the call budget. `# ASSUMPTION:` single-attribute limit and rate limits; verify against current documentation and record in `docs/ASSUMPTIONS.md`.

### Step 6 — Session provider (`auth/session.py`)
`SessionProvider` protocol and `StsSessionProvider` (assume-role through STS).
**Tests first (Stubber for STS):** the role ARN is built correctly from account and role name; `ExternalId` and a session name that includes the purpose are passed; duration is at most 3600 seconds; credentials are cached until shortly before expiry and refreshed afterwards (injected clock); an account **not in the workspace's approved account list** raises `OutOfScopeAccount` **before any STS call is made**; `repr` and `str` never show credentials; credentials are never written to logs (test with `caplog`).
Live wiring (real STS client) is `# SKELETON:`; no test calls it.

### Step 7 — Capability probe (`probe.py`)
Maps snapshot statuses to `SourceAvailability` per account and source.
**Tests first:** `ok` with data gives `available`; `ok` with no data gives `empty`; each other status maps to its namesake; a missing snapshot is `error` with a note; the result lists accounts and sources in sorted order.

### Step 8 — In-memory graph store
`GraphStore` protocol and `InMemoryGraphStore` (networkx-backed).
**Tests first:** adding the same resource twice is idempotent and merges `sources` and tags; adding the same edge twice merges its evidence without duplicates; neighbour queries return sorted results; resources from several accounts coexist; export of all resources and edges is in a stable sorted order; removing nothing is possible (the store is append-only for the build run).

### Step 9 — Cross-account handling (`graph/crossaccount.py`)
Input: the store, the workspace's approved account list, `known_aws_service_account_ids`.
**Tests first:** an edge to Account B, where B is approved and its resources are present, resolves to a real edge; where B is approved but not yet collected, the edge stays `pending` and is reported; where B is not approved, the destination becomes an `ExternalRef(reason="out_of_scope")` and **no access is attempted**; where B is in `known_aws_service_account_ids`, the reason is `aws_owned` and it is not reported as a finding; the same external account referenced many times is reported once with a count; cross-account edges never influence membership by default (weight 0.0) but are included as `external_refs` and dependencies on the affected workloads.

### Step 10 — Graph builder (`graph/*`)
Implement in this order, each with its own tests, then `build.py` to orchestrate.
1. **Seeds (`seeds.py`).** Each stack group and each (tag key, value) group becomes a seed. Seeds that share at least one resource are merged (union-find over seed membership). A resource in two seeds that disagree is **not** an error: the seeds merge and the merge is recorded in evidence. *Tests first:* two stacks with no overlap stay separate; two stacks linked by one tagged resource merge; a tag group spanning two stacks merges them; a lone untagged resource is in no seed; a resource with a tag conflict (Step 4) is excluded from seeds; seed ids are stable under input shuffling.
2. **Shared infrastructure (`shared.py`).** A container or hub resource (VPC, load balancer, shared security group) that links to resources of at least `shared_min_clusters` different seeds is marked shared and is **not merged** into any cluster. *Tests first:* a load balancer fronting targets from two stacks is shared and the two workloads stay separate; a load balancer fronting one stack's targets attaches normally; a VPC containing two workloads is shared.
3. **Attach (`attach.py`).** For each resource not in a seed, compute affinity to each cluster (Section 7). Attach to the best cluster if affinity ≥ `attach_threshold` **and** it beats the second best by ≥ `attach_margin`; otherwise leave it for residual handling and record why (`below_threshold` or `ambiguous`). *Tests first:* hand-computed affinities (assert exact values, for example subnet 0.4 and shared security group 0.4 and same principal 0.3 give `1 − 0.6·0.6·0.7`); one weak edge alone does not attach; many members linked by the same edge type count once; tie-breaking is deterministic (lowest workload id); ambiguous resources are not attached and are reported.
4. **Residual (`residual.py`).** Unattached resources with a VPC are grouped by `(account, region, vpc)` into `ResidualGroup` with confidence `low`; resources with no usable edges go to `unmapped`. *Tests first:* residual groups are formed per VPC; an isolated resource is `unmapped`; shared infrastructure is never in residual groups.
5. **Confidence (`confidence.py`).** `high` if the declared share of members ≥ `confidence_high_declared_ratio`, `medium` if ≥ `confidence_medium_declared_ratio`, otherwise `low`. A workload built from a single source can be at most `medium`. *Tests first:* boundary values at exactly each ratio; single-source cap.
6. **Name hint.** Deterministic heuristic: the most common workload tag value among members, else the most common stack name, else `"workload-<id>"`. Ties break alphabetically. *Tests first:* each branch; deterministic ties. `# SKELETON:` naming will later be replaced by an LLM step behind an interface.
7. **`build.py`.** `build_workloads(store, ruleset, scope, probe, snapshot_ids) -> BuildResult` runs steps 1–6 and cross-account resolution, and fills `notes` with every degradation (for example, "cloudtrail denied in 222222222222").

### Step 11 — Determinism and invariants (`tests/determinism/`)
**Tests first (these are the most important tests in the project):**
- Shuffling the order of resources, edges and snapshots yields an **identical** serialised `BuildResult` (use `hypothesis` to generate permutations over every golden scenario).
- Running the build in subprocesses with `PYTHONHASHSEED` set to at least three different values yields identical output (compare SHA-256 of the output).
- **Partition invariant:** every input resource appears in exactly one of: a workload's members, shared infrastructure, a residual group, or unmapped. None is dropped or duplicated.
- **Evidence invariant:** every attached or declared member has at least one evidence record; every edge in the output refers to existing evidence.
- Changing the ruleset changes `ruleset_hash` and nothing else about identity rules.
- Re-running with the same input gives the same `workload_id`s.

### Step 12 — Evaluation (`evaluation/`)
**Tests first (small, hand-computed cases):** pairwise precision, recall and F1 over resource pairs (assert exact numbers on a 5-resource example); coverage (share of resources in any workload); unmapped rate; perfect prediction gives 1.0 on all; empty prediction gives recall 0.0; shared infrastructure is excluded from pair counts and reported separately; the golden loader rejects files that reference unknown resources.
Provide a function `evaluate(build_result, golden) -> EvaluationReport` and a printable summary.

### Step 13 — Pipeline and CLI
`pipeline.run(snapshots_dir, ruleset, workspace) -> BuildResult` performs: load snapshots → probe → normalise → store → cross-account resolution → build → write JSON.
CLI (argparse, no extra dependency): `workload-graph build --snapshots DIR --workspace FILE --ruleset FILE --out FILE` and `workload-graph evaluate --result FILE --golden FILE`. `workload-graph collect` exists but raises `NotImplementedError("SKELETON: live wiring requires approved roles")`.
**Tests first:** `build` over a scenario directory writes a valid `BuildResult` JSON; exit codes (0 success, 2 bad input); an unreadable snapshot is reported and does not crash the run (degradation note); output is identical on repeat; `collect` refuses to run.

### Step 14 — Optional Parquet store
Only after Steps 1–13 are green. Behind the same `GraphStore` protocol; `pyarrow` is an optional extra. **Tests first:** round-trip of resources and edges; stable ordering; the contract test suite for `GraphStore` runs against both implementations.

---

## 9. Golden scenarios (`tests/fixtures/raw/` and `tests/fixtures/golden/`)

Hand-write each as raw snapshots plus an expected-workloads file. Describe each in `docs/SCENARIOS.md`. All use fake accounts `111111111111` (and `222222222222` where noted).

| # | Scenario | Expected behaviour |
|---|---|---|
| S1 | Clean: two stacks, each with instances, a database, a load balancer | Two high-confidence workloads |
| S2 | Tags only, no stacks (`app=orders`, `app=billing`) | Two workloads from tags, high confidence |
| S3 | Tag conflict: one resource tagged `app=orders` and `app=billing` | Resource excluded from both and reported; the two workloads stay separate |
| S4 | One load balancer fronts targets from two stacks | Two workloads, load balancer in `shared_infrastructure` touching both |
| S5 | Nothing declared; only VPCs and subnets | Residual groups per VPC, all low confidence; no workloads |
| S6 | Undeclared resource created by the same principal and window as a stack | Attached with affinity and low or medium confidence, reasons listed |
| S7 | Nested stacks | Child resources belong to the child group; parent link recorded |
| S8 | Cross-account: peering to approved Account B and to unapproved Account C | B resolves or is pending; C is an `ExternalRef` and a reported dependency |
| S9 | Source denied: CloudTrail returns access denied | Workloads still built; `notes` and `probe` show the degradation |
| S10 | Empty account | Empty result with probe showing `empty` |
| S11 | Ambiguous: a resource linked equally to two clusters | Not attached; reported as `ambiguous` |
| S12 | Large synthetic case (generated, around 2,000 resources) | Completes in a reasonable time; partition invariant holds |

Each golden file lists expected workloads as sets of resource keys. Evaluation (Step 12) runs over S1–S11 and prints precision, recall, coverage and unmapped rate. **Do not tune rules to reach perfect scores; record what the numbers show in `docs/STATUS.md`.**

---

## 10. Cross-cutting requirements

### 10.1 Determinism checklist
Sort before iterating any set or dict that affects output; never depend on set ordering; no `random` without a fixed seed (prefer none); no wall-clock time in identity or hash computation; the clock is injected wherever time matters; JSON output uses `sort_keys=True` and stable list ordering.

### 10.2 Safety
Collectors may only call operations in their allowlist (tested). Never log credentials, tokens or full API payloads. `logging_setup.py` configures a plain structured logger; add a test that a sample run with fake credentials produces no log line containing them.

### 10.3 Typing and style
Strict mypy on `src/`. Ruff clean. No `Any` in public signatures unless commented with the reason.

### 10.4 Markers (grep-able)
`# SKELETON:` placeholder to replace · `# TODO(input-needed):` blocked on information from a named person or system · `# ASSUMPTION:` guess the design relies on (also copied to `docs/ASSUMPTIONS.md`) · `# DECISION-PENDING:` undecided design choice (also in `docs/OPEN_QUESTIONS.md`) · `# RISK:` something dangerous if done wrong. Each is followed by a one-line explanation.

---

## 11. Documentation to produce
- `README.md`: purpose, the reasoning chain (Section 1, condensed), setup, how to run checks, CLI usage, marker legend.
- `docs/DECISIONS.md`: Section 2's table plus every decision you make, each `provisional`.
- `docs/ASSUMPTIONS.md`: seed with: CloudTrail event history covers management events for 90 days; `lookup_events` allows one lookup attribute per call and is rate limited; partition `aws` only; stack resource listings return physical IDs, not ARNs; tagging conventions vary by customer.
- `docs/OPEN_QUESTIONS.md`: seed with: which tag keys customers actually use; whether the approved account list can be read from the organisation or must be supplied; whether to add AppRegistry or Config as optional sources and when; how shared infrastructure should be presented to TAMs; acceptable precision and recall; what the TAM-facing confirmation workflow looks like; data retention for snapshots.
- `docs/EDGE_TYPES.md`, `docs/SCENARIOS.md`, `docs/STATUS.md` as described above.

---

## 12. Definition of done and final report

- [ ] Python 3.11 venv created inside the project; dependencies installed only there.
- [ ] All 14 steps done in order, each started with failing tests.
- [ ] `make check` passes (lint, strict typecheck, tests) offline; coverage targets met.
- [ ] Determinism tests pass, including the multi-`PYTHONHASHSEED` test.
- [ ] Read-only enforcement test passes for every collector.
- [ ] Scenarios S1–S12 pass; evaluation numbers recorded in `docs/STATUS.md`.
- [ ] No credentials, real IDs or customer data; the word "clients" appears nowhere.
- [ ] All docs in Section 11 exist and are seeded.
- [ ] Final message lists: what was built, what is real versus `SKELETON`, test counts (passed, skipped, failed), evaluation numbers, every decision you made on your own, the five biggest open questions, and the first three things you recommend doing once real account data is available.

---

## 13. Guidance for later changes (important)

When real snapshots, tag conventions or TAM feedback arrive:
1. Read `docs/STATUS.md`, `docs/OPEN_QUESTIONS.md`, `docs/ASSUMPTIONS.md` and `docs/DECISIONS.md` first.
2. Treat anything marked `SKELETON`, `ASSUMPTION` or `DECISION-PENDING` as provisional, not as a constraint.
3. Add real (anonymised) cases as new golden scenarios **before** changing rules; let the evaluation numbers show whether a change helps.
4. Add a new source only when a scenario shows a gap that existing sources cannot fill; give it the same shape (collector, normaliser, allowlist, fixtures, tests).
5. If a module boundary turns out wrong, change it and record the reason in `docs/DECISIONS.md`.
6. When something is ambiguous, ask the user. Do not guess.

