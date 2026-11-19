# Open questions

Each item is unresolved. The code picks the safest reversible option and records it in `DECISIONS.md` where a choice was required.

1. **Which tag keys do customers actually use?** Defaults are `app`, `application`, `workload`, `project`, `service`, matched case-insensitively. `# TODO(input-needed):` replace them from real accounts before tuning anything else.
2. **Where does the approved account list come from?** Today it is a workspace file the operator supplies. It is unknown whether it can be read from AWS Organizations.
3. **When, if ever, should AppRegistry or AWS Config be added?** Not in this version. Add one only when a golden scenario shows a gap the four current sources cannot fill.
4. **How should shared infrastructure be presented to TAMs?** The build lists each shared resource and the workload ids it touches. The presentation (a dependency, a footnote, a separate section) is not decided.
5. **What precision and recall are acceptable?** S1–S11 are scored in `STATUS.md`. Precision is 1.0 on every scenario. Recall is below 1 where a human would keep an exclusive subnet or security group inside the workload and the builder leaves it in a residual group. No target number has been agreed.
6. **What does the TAM-facing confirmation workflow look like?** This component stops at a proposal. Accept, split, merge and rename are out of scope.
7. **How long are snapshots retained, and who may read them?** Not decided. The store is a directory of JSON files with no retention policy.
8. **Should an exclusive subnet or security group join the only workload that uses it?** Container edges alone score `1 - (1-0.4)*(1-0.2) = 0.52`, under the 0.6 attach threshold, so they stay residual (D35, D22). Weights were not changed. Decide from real cases before editing the ruleset.
9. **Is a self-edge the right representation of a one-member tag group?** It puts the resource in a seed (D18). A different representation is easy to swap if it causes confusion.
10. **Is the evidence cap of 10 enough for a TAM to audit a member?** It is a `SKELETON` (D23).
11. **What is the lockfile strategy?** `requirements/*.txt` uses ranges, not pins (`# SKELETON` in `requirements/base.txt`).
12. **VPC Flow Logs, cost, a UI, and LLM naming** were considered and left out of scope. Naming stays a deterministic heuristic until an interface for a later step exists.

## Cost layer

13. **Who owns the management account?** Payer mode (one access account, many linked) and per-account mode are both supported; which one customers use is unknown.
14. **Will customers activate a workload tag as a cost allocation tag?** Without it, the tag method never applies.
15. **Should amortised cost be the default metric?** Today it is `UnblendedCost`.
16. **How should shared infrastructure cost be allocated?** Not allocated (F-D7).
17. **Do customers' Support plans allow adding Trusted Advisor next?**
18. **What review format do TAMs want?** The Ledger screen is a first answer.
19. **What freshness is acceptable for cached cost data?** 24 hours today.
