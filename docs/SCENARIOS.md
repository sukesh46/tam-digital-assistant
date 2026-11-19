# Golden scenarios

Accounts are fake: `111111111111` (A), `222222222222` (B, approved in S8) and `333333333333` (C, never approved). The customer is `example-customer`. Region is `eu-west-1` unless a resource is global (S3).

Raw snapshots for S1–S11 are produced by `tests/fixtures/build_scenarios.py`. Golden files in `tests/fixtures/golden/` are the human expectation, written by hand. S12 is generated in the test.

| # | What the fixtures contain | What the spec expects | What the build does |
|---|---|---|---|
| S1 | Stacks `orders` and `billing`, each with instances, a database, a load balancer, a target group and a security group. Subnet per stack. One shared VPC. Tags are names only. CloudTrail is empty | Two high-confidence workloads | Two high-confidence workloads. The VPC is shared. The exclusive subnets fall into a residual group because container affinity is 0.52 |
| S2 | No stacks. `app=orders` on two instances, a database and an S3 bucket. `app=billing` on an instance and a function. One VPC, subnet and security group used by all three instances | Two workloads from tags, high confidence | Two high-confidence workloads (`tags` and `topology`). The VPC, subnet and security group are shared |
| S3 | `app=orders` and `app=billing` on four instances, plus `i-0s3both` tagged `app=orders` and `App=billing`. Other sources empty | The conflicted resource is in neither workload; the two workloads stay separate | Two medium-confidence workloads (single source). `i-0s3both` is unmapped with reason `tag_conflict` |
| S4 | Stacks `orders` and `billing`, each with an instance and a target group. `shared-alb` is in neither stack and fronts both | Two workloads. The load balancer is shared and touches both | As expected. The shared subnet and VPC are shared as well |
| S5 | Two VPCs, subnets, one security group, three untagged instances. Nothing declared | Residual groups per VPC, low confidence, no workloads | Two residual groups, `vpc-0s5a` and `vpc-0s5b`, both low. No workloads |
| S6 | Stack `orders` (instance and database). `i-0s6new` shares the subnet and security group and was created by the same principal inside the window | Attached, with affinity and reasons, low or medium confidence | Attached at affinity 0.7984, medium confidence, reasons `CREATED_BY_SAME_PRINCIPAL`, `SAME_SUBNET`, `SAME_VPC`, `SHARED_SECURITY_GROUP`. The subnet and security group stay residual |
| S7 | Parent stack `platform` holds a nested stack and an SNS topic. The child holds a VPC and a subnet | Child resources stay with the child. The parent link is recorded | Two workloads. The child note and `parent_stack_id` record the parent. They are not merged |
| S8 | Approved accounts A and B. A's stack peers to B's VPC (collected) and to C's VPC (not approved) | B resolves or stays pending. C is an `ExternalRef` and a reported dependency | B's VPC is a resolved dependency. C is an external finding. Nothing is pending |
| S9 | A small orders stack. CloudTrail status is `denied` | Workloads are still built. Notes and the probe show the denial | One high-confidence workload. Probe `cloudtrail=denied`. Note `cloudtrail denied in 111111111111` |
| S10 | All four sources empty | Empty result, probe `empty` | No resources, no workloads. All four probe entries are `empty` |
| S11 | Two stacks. `i-0s11mystery` shares the subnet, security group and VPC equally | Not attached. Reported as ambiguous | Both workloads stay separate. The instance is in the residual group with reason `ambiguous`, and a note names both workload ids |
| S12 | Generated, about 2,000 resources: 100 stacks, undeclared instances, tag groups, shared load balancers | Finishes in a reasonable time. The partition invariant holds | Covered by `tests/scenarios/test_scenarios.py` and `tests/determinism/test_invariants.py`. The limit under test is 60 seconds |

Evaluation against the golden files is in `docs/STATUS.md`. Recall below 1.0 is concentrated on exclusive subnets and security groups (S1, S6, S8, S9). See open question 8.
