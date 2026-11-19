# Status

## Toolchain (installed in `.venv`)

| Tool | Version |
|---|---|
| Python | 3.11.7 |
| pip | 26.3 |
| pytest | 9.2.0 |
| pytest-cov | 7.1.0 |
| hypothesis | 6.170.15 |
| ruff | 0.17.5 |
| mypy | 2.4.1 |
| boto3 / botocore | 1.44.4 / 1.44.4 |
| pydantic | 2.14.3 |
| networkx | 3.6.1 |
| types-networkx | 3.6.1.20261114 |
| pyarrow (optional extra) | 26.0.1 |

`make check` (ruff, strict mypy, pytest): **461 passed, 0 skipped, 0 failed**. Line coverage of `src/workload_graph` is 97%. `graph/`, `normalisers/`, `models/`, `ids.py` and `ruleset.py` are each above 90% (`normalisers/topology.py` is 91%, `normalisers/cloudtrail.py` is 98%).

## Modules

| Step | Module | Status | Tests | Open issues |
|---|---|---|---|---|
| 1 | scaffolding, venv, tooling | real | `test_scaffolding.py` | none |
| 2 | `ids.py`, models | real | `test_ids.py`, `test_models.py` | partition `aws` only (D13) |
| 3 | `ruleset.py` | real | `test_ruleset.py` | every default is provisional |
| 4 | normalisers | real | `test_normaliser_*.py`, `test_fixture_shapes.py` | `CREATED_TYPES` is an assumption |
| 5 | collectors | real | `test_collectors.py` | live calls are not wired |
| 6 | `auth/session.py` | partial | `test_session.py` | `boto3_session` is `SKELETON` |
| 7 | `probe.py` | real | `test_probe.py` | none |
| 8 | in-memory store | real | `test_store_contract.py` | none |
| 9 | `graph/crossaccount.py` | real | `test_crossaccount.py` | AWS-owned account list is empty |
| 10 | graph builder | real | `test_graph_*.py` | name hint is `SKELETON`; evidence cap is `SKELETON` |
| 11 | determinism, scenarios S1–S12 | real | `tests/determinism/`, `tests/scenarios/` | exclusive containers stay residual |
| 12 | evaluation | real | `test_evaluation.py` | metric definition is D34, provisional |
| 13 | `pipeline.py`, `cli.py` | partial | `test_pipeline_cli.py` | `collect` is `SKELETON` and refuses to run |
| 14 | `store/parquet.py` | real | contract suite plus round-trip | optional extra; not on the core install path |

`models/workspace.py` is partial: the role name default is a placeholder. `graph/naming.py` is partial for the same reason as the name-hint skeleton.

## Evaluation (S1–S11)

Scored by `evaluate` against `tests/fixtures/golden/`. Shared infrastructure is excluded from pair counts. Precision is 1.0 when the build predicts no pairs (S5 and S10). Weights were not tuned.

| Scenario | Precision | Recall | F1 | Coverage | Unmapped rate | Pairs (predicted/golden/matched) |
|---|---|---|---|---|---|---|
| S1 | 1.0000 | 0.7347 | 0.8471 | 0.8667 | 0.0000 | 36/49/36 |
| S2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 7/7/7 |
| S3 | 1.0000 | 1.0000 | 1.0000 | 0.8000 | 0.2000 | 2/2/2 |
| S4 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 6/6/6 |
| S5 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0/0/0 |
| S6 | 1.0000 | 0.4000 | 0.5714 | 0.5714 | 0.0000 | 6/15/6 |
| S7 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 4/4/4 |
| S8 | 1.0000 | 0.5000 | 0.6667 | 0.6000 | 0.0000 | 3/6/3 |
| S9 | 1.0000 | 0.5000 | 0.6667 | 0.6000 | 0.0000 | 3/6/3 |
| S10 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0/0/0 |
| S11 | 1.0000 | 1.0000 | 1.0000 | 0.8000 | 0.0000 | 2/2/2 |

What the numbers show:

- The builder does not merge two human workloads. Precision is 1.0 on every scenario, including the shared load balancer (S4) and the ambiguous instance (S11).
- Recall falls where the golden file places an exclusive subnet or security group inside the workload and the builder leaves it in a residual group (S1, S6, S8, S9). Container-edge affinity is 0.52, below the 0.6 threshold. See `OPEN_QUESTIONS.md` item 8. This was not "fixed" by raising weights.
- S5 has no workload pairs on either side, so the pairwise scores are 1.0 and coverage is 0. Coverage is the number that matches the expected behaviour (no workloads proposed).
- S3 coverage is 0.8 because the conflicted instance is unmapped on purpose.
- S11 coverage is 0.8 and the unmapped rate is 0 because the ambiguous instance sits in a residual group (D37), with a note that names both workloads.
- S6 attaches `i-0s6new` at affinity 0.7984 with medium confidence. The recall gap is the subnet and security group, not the instance.

S12 is a scale and invariant check, not a scored golden file.
