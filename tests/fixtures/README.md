# Test fixtures

**Fake data only. Never put real customer data, real account ids, real ARNs or credentials here.**

Accounts used: `111111111111` (A), `222222222222` (B, approved in S8), `333333333333`
(C, never approved). The customer is always `example-customer`.

- `raw/<scenario>/<account>/<source>.json` are `RawSnapshot` files. Their response pages are
  shaped like real boto3 responses; `tests/unit/test_fixture_shapes.py` validates every page
  against the botocore service models, so fixtures cannot drift from real response shapes.
  Page keys starting with `_` are collector metadata (request context), not API data.
- `raw/unit/` holds the hand-written fixtures used by the normaliser unit tests.
- `golden/<scenario>.json` lists the workloads a human would expect, as sets of resource keys.
- `build_scenarios.py` writes the golden-scenario raw snapshots (S1 to S11) from compact,
  hand-written declarations. Run it with `python tests/fixtures/build_scenarios.py` after
  editing; its output is committed. S12 is generated at test time.
