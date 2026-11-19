# Decisions

Every decision is **provisional**. Revisit the ones marked with a trigger in the table, and treat anything tagged `SKELETON` in code as replaceable.

## Specified decisions

| # | Decision | Reason | Status | Revisit when |
|---|---|---|---|---|
| D1 | Python 3.11 only (`>=3.11,<3.12`) | Required by the user; keeps the toolchain consistent | provisional | The user changes the target |
| D2 | `venv` + `pip` + `pyproject.toml`, `src` layout | Simplest portable setup; the `src` layout prevents accidental imports from the working directory | provisional | Packaging needs grow |
| D3 | `pydantic` v2 for models | Validation at the boundaries, JSON round-trip, immutability | provisional | Performance problems |
| D4 | `networkx` for the in-memory graph only | Tested graph structure and neighbour queries; clustering logic stays ours | provisional | Clustering experiments |
| D5 | Own seed-and-attach algorithm, no ML or community detection initially | Explainable, deterministic, easy to test | provisional | Golden metrics show a gap |
| D6 | `botocore.stub.Stubber` for AWS tests; no `moto` | Exact service-model validation, no extra dependency, no hidden behaviour | provisional | Moto coverage becomes necessary |
| D7 | `hypothesis` for property tests | Invariants (determinism, partition) are best tested with generated inputs | provisional | None |
| D8 | Raw snapshots stored as JSON and replayable | Lets the builder run offline on stored data and makes failures reproducible | provisional | None |
| D9 | Four sources only (tags, stacks, topology, seeded CloudTrail) | Deterministic, low volume, mostly default-available | provisional | Evaluation shows gaps |
| D10 | Canonical key = (account_id, region, arn) | ARNs alone are not unique for some resources (such as S3 buckets) and account is needed for cross-account joins | provisional | None |
| D11 | Bounded affinity score | One contribution per edge type per cluster, so many weak edges cannot pile up | provisional | Tuning with golden data |
| D12 | Ruleset is versioned, hashed and in the output | Any result can be reproduced and compared across rule changes | provisional | None |
| D13 | `partition = "aws"` only | China and GovCloud partitions are out of scope until requested | provisional | A customer needs them |
| D14 | Parquet store is optional (extra) | Avoids a heavy dependency in the core path | provisional | Athena integration |

## Decisions made while building

| # | Decision | Reason | Status | Revisit when |
|---|---|---|---|---|
| D15 | `Workspace` lives in `models/workspace.py` | The approved account list is an input to the session provider, the probe and the builder. The layout in the spec did not name a module for it | provisional | A different scope object is supplied |
| D16 | Name hint lives in `graph/naming.py` | Keeps `build.py` to orchestration. The heuristic is the spec's, and is marked `SKELETON` because an LLM step may replace it later | provisional | The naming interface is designed |
| D17 | Tests ignore ruff `S105`, `S106`, `S311` and `S603` | Tests use obviously fake credentials, a seeded RNG for shuffles and synthetic data, and a fixed-argument subprocess for `PYTHONHASHSEED`. The spec already ignores `S101` in tests | provisional | The security lint set changes |
| D18 | A tag group is a star to the smallest member key. A group of one gets a self-edge | Same reason as container edges: connectivity without a quadratic number of edges. The self-edge keeps a lone tagged resource inside a seed | provisional | A different tag topology is wanted |
| D19 | A tag conflict excludes the resource from seeds and from attachment. It is unmapped with reason `tag_conflict` | The spec says the resource belongs to neither workload until a human decides. Letting it attach on topology would undo that | provisional | TAMs want a conflict to still be a candidate |
| D20 | Nested stacks are not merged into the parent | The spec says the child's resources belong to the child group and the parent link is recorded. The link is the attribute `parent_stack_id` plus a note, not a `STACK_MEMBER` edge | provisional | TAMs want one workload per root stack |
| D21 | Only an undeclared hub can be shared. Containers count distinct seeds on incoming container edges; a load balancer or target group counts them on outgoing `LB_TARGET` edges | Declared resources are seeds, not shared infrastructure. Direction stops a resource from looking shared just because it points at a popular container | provisional | Real data shows declared hubs that should be shared |
| D22 | Attachment is one pass. A link is a direct edge, or both resources pointing at the same container. Affinity is rounded to 6 decimal places. A tie goes to the lowest seed id | One pass is reversible and explainable. Rounding keeps the margin comparison stable. The lowest id is an order-independent tie break | provisional | A second pass would change golden scores |
| D23 | At most 10 evidence records are kept on a member (`MEMBER_EVIDENCE_CAP`) | Bounds output size. Marked `SKELETON` | provisional | TAMs need the full evidence list |
| D24 | `UnplacedReason` includes `no_edges` as well as `below_threshold` and `ambiguous` | A resource with no usable link is a different fact from one that scored below 0.6 | provisional | The report shape is fixed with TAMs |
| D25 | `BuildResult` carries `pending_refs` | An approved account that has not been collected yet must stay visible. It is not an external finding and it is not a member | provisional | Pending refs are folded into notes only |
| D26 | Degradation notes use `{source} {availability} in {account}` | One stable sentence per probe entry that is not `available`, so notes sort and diff cleanly | provisional | A structured degradation list is preferred |
| D27 | Mixed regions for one account and source: any region with data makes the account `partial`; otherwise denied beats error beats empty | A TAM should see that some data arrived. A total denial should not be hidden by an empty region | provisional | Region-level probe output is required |
| D28 | Stack collection uses `DescribeStackResources` | It returns the resource list in one call. The 100-resource ceiling is an assumption, recorded in `ASSUMPTIONS.md` | provisional | A stack larger than 100 resources appears |
| D29 | Collector request context is stored under payload keys that start with `_` | Normalisers need seed ids and target-group ARNs that are not fields of the AWS response. `has_data()` ignores those keys | provisional | Snapshots gain a separate metadata object |
| D30 | The CloudTrail call budget applies to one `collect` call. After a denial, later calls of that same operation are skipped | Bounds cost (D9) and avoids repeating a call that already failed | provisional | Budgets need to be per account across collectors |
| D31 | CloudTrail pass 2 keeps events only for principals that created a seed in pass 1 | Matches the seeded design: cost scales with known creators, not with every event name in the account | provisional | Undeclared creators turn out to matter |
| D32 | The stack resource itself is a declared member of its seed | The stack is the declaration. Omitting it made workload membership disagree with the stack the TAM can see | provisional | Stacks should be metadata only |
| D33 | `sources_used` is the union of the sources on member resources | A workload built from tags plus topology is not single-source, so the medium cap does not apply. This is what makes S2 high confidence | provisional | Source confidence should be counted differently |
| D34 | Pairwise precision is 1.0 when the build predicts no pairs. Coverage and the unmapped rate exclude shared infrastructure from the denominator | No predicted pair means no false positive. Shared infrastructure is reported separately, as the spec requires | provisional | A TAM-facing metric definition is agreed |
| D35 | Golden files record a human grouping, including an exclusive subnet with the workload that uses it. Rule weights were not changed to raise recall | The spec says to record the numbers, not to tune for a perfect score. S1, S6, S8 and S9 recall is below 1 for this reason | provisional | Real golden cases replace these files |
| D36 | S1–S11 raw snapshots are generated by `tests/fixtures/build_scenarios.py` from compact declarations | The pages still validate against botocore. Hand-editing thousands of response fields would drift | provisional | A fixture needs a shape the generator cannot express |
| D37 | An ambiguous or below-threshold resource that has a VPC goes to a residual group, not to `unmapped` | Matches the residual rule. S11's mystery instance is residual with reason `ambiguous`, and the note still says ambiguous. The unmapped rate therefore does not count it | provisional | TAMs want ambiguous resources listed only under unmapped |
| D38 | Each workload carries an edge list: store edges whose source is a member, sorted by source, destination and type | The page needs lines and edge tooltips. Sorting makes the list deterministic. LLM naming stays out of this path | provisional | A different edge subset is wanted on the page |
| D39 | `workload-graph ui` serves a local page. The operator types the account id. Collection runs only when that id matches the STS caller, using the ambient credential | The test pad is a personal account, not a member account reached through a role. The id is an input, not a value stored in the project | provisional | Approved-role collection is wired |
