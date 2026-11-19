# workload-graph

Reconstructs **candidate workloads** from read-only snapshots of a customer's AWS accounts. A workload here is a business idea, not something AWS can name for you. The output is a proposal: each group has a confidence level and the evidence behind every member. A human confirms it. Downstream pieces of the TAM Digital Copilot (findings, health scores, QBR drafts) are not part of this package.

## Why it is shaped this way

1. TAMs need a per-workload view, and customer accounts are often untagged or inconsistent.
2. There is no ground truth to converge on. Shared services belong to several workloads. The result is a proposal with evidence.
3. Start with a few deterministic, low-volume sources. Add another only when evaluation shows a gap.
4. CloudTrail is queried from seeds we already know, so cost follows the seeds rather than the log volume. VPC Flow Logs are out of scope.
5. Not every source is turned on. The probe reports what was available. More sources mean a more trustworthy proposal.
6. Declared groupings (stacks, workload tags) are seeds. Weaker signals only attach a resource above a threshold. Everything else is low confidence or unmapped.
7. Shared infrastructure must not merge workloads. Only grouping declarations merge seeds.
8. Every grouping traces back to evidence. The rules are simple enough to audit.
9. A reference to another account is metadata, not permission. It resolves only if that account is in the approved scope.
10. Collectors do I/O and return raw snapshots. Normalisers and the builder are pure functions, so the suite runs offline.
11. Fixtures are checked against botocore's service models, so they cannot drift from real response shapes.
12. The golden scenarios include untagged, ambiguous and cross-account cases. The scores are in `docs/STATUS.md`.

## Setup

Python 3.11 is required. Do not use another version.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/dev.txt
python -m pip install -e .
```

Parquet storage is optional:

```bash
python -m pip install -r requirements/parquet.txt
```

## Checks

```bash
make check              # ruff, strict mypy, full pytest
make test-unit
make test-scenarios
make test-determinism
```

Tests never call AWS or the network. An autouse fixture rejects socket connections.

## CLI

`build` and `evaluate` read files on disk. They do not contact AWS.

```bash
workload-graph build \
  --snapshots tests/fixtures/raw/s1 \
  --workspace tests/fixtures/raw/s1/workspace.json \
  --ruleset ruleset.json \
  --out out/s1.json

workload-graph evaluate --result out/s1.json --golden tests/fixtures/golden/s1.json
```

Write a ruleset with `Ruleset().save(path)` from `workload_graph.ruleset`, or pass a JSON file of the same shape. Exit code 0 is success. Exit code 2 is bad input (missing files, invalid JSON, a golden key that is not in the result).

`workload-graph collect` refuses to run. Live assume-role wiring needs approved roles and is not implemented.

Snapshots are `raw/<account-id>/<source>.json` files (`cloudtrail`, `stacks`, `tags`, `topology`). An unreadable file is reported on the result and the run continues.

## Markers

| Marker | Meaning |
|---|---|
| `# SKELETON:` | Placeholder to replace |
| `# TODO(input-needed):` | Blocked on a fact from a named person or system |
| `# ASSUMPTION:` | A guess the design relies on. Copied to `docs/ASSUMPTIONS.md` |
| `# DECISION-PENDING:` | An undecided design choice. Copied to `docs/OPEN_QUESTIONS.md` |
| `# RISK:` | Dangerous if done wrong |

Use the word **customers**. The project directory is the boundary: do not install packages globally, and do not commit unless asked.

## Further reading

- `docs/DECISIONS.md` — D1–D14 and the choices made while building
- `docs/ASSUMPTIONS.md` — what has not been checked against a live account
- `docs/OPEN_QUESTIONS.md` — unresolved questions
- `docs/EDGE_TYPES.md` — what each edge means and how it can be wrong
- `docs/SCENARIOS.md` — S1–S12
- `docs/STATUS.md` — toolchain, module status, evaluation numbers
- `CURSOR_INSTRUCTIONS.md` — the build specification
