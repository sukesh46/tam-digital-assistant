# Edge types

Edges are directed and each one carries at least one evidence record. Grouping declarations form seeds. Other edges attach a resource, mark infrastructure as shared, or record a fact. They do not merge seeds.

Container edges point from the resource to the container (subnet, VPC or security group). Two resources are treated as linked when they share a container. Pairwise edges inside a subnet are not created.

| Edge | Produced by | Role | How it can be wrong |
|---|---|---|---|
| `STACK_MEMBER` | CloudFormation stack normaliser. Every live stack resource, and the stack itself, points at the stack | Grouping declaration. Forms a seed. Not weighted | A stack can hold more than one product. `DELETE_COMPLETE` stacks are ignored. Nested stacks stay with the child (D20) |
| `TAG_WORKLOAD_KEY` | Tags normaliser, for keys in `workload_tag_keys`. Resources that share a value form a star to the smallest key. A single resource gets a self-edge | Grouping declaration. Forms a seed. Not weighted | The key may not mean "workload" for this customer. Two different values on one resource are a conflict: no edges, and the resource is excluded (D19) |
| `LB_TARGET` | Topology normaliser. From a target group, and from each of its load balancers, to each resolvable target | Attach hint. Weight 0.8. Also used to detect a shared load balancer | An IP target is a note, not an edge. A target group reused across products makes the load balancer look shared, which is intended, or attaches a stray target |
| `SAME_SUBNET` | Topology normaliser. Resource to its subnet | Attach hint. Weight 0.4. Incoming edges are how a subnet counts the seeds it touches | Co-location is not ownership. Many products share a subnet. Affinity of the subnet itself to one workload is 0.52 with `SAME_VPC`, below the attach threshold |
| `SHARED_SECURITY_GROUP` | Topology normaliser. Resource to its security group | Attach hint. Weight 0.4. Same container rules as a subnet | A shared group is common platform plumbing. The name of the edge type is the relationship, not a claim that the group is shared infrastructure |
| `SAME_VPC` | Topology normaliser. Resource to its VPC | Attach hint. Weight 0.2. A VPC touching two seeds becomes shared infrastructure | A VPC is a weak hint. It must not merge workloads (D5, D21) |
| `CREATED_BY_SAME_PRINCIPAL` | CloudTrail normaliser. Two creations by the same principal whose times differ by at most `cloudtrail_window_minutes`, inclusive, inside the lookback measured from `collected_at` | Attach hint. Weight 0.3 | A platform role creates many unrelated resources. The window and the event-name list are provisional |
| `CROSS_ACCOUNT_REF` | Topology (peering, transit gateway attachment) and CloudTrail (identity account different from recipient account) | Informational. Weight 0.0, so it does not affect membership. Resolved if the other account's resource is in the store, pending if that account is approved but not collected, external otherwise | A reference is not permission. Account C stays an `ExternalRef`. An AWS-owned account is not a finding once its id is listed in `known_aws_service_account_ids` |
