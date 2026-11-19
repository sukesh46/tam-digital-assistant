# Assumptions

Each of these is also marked `# ASSUMPTION:` in code. None of them has been checked against a live account. Verify before trusting a result that depends on one.

| Assumption | Where | What to verify |
|---|---|---|
| CloudTrail event history covers management events for 90 days | `ruleset.py` (`cloudtrail_lookback_days`) | Current CloudTrail lookup retention |
| `LookupEvents` accepts one lookup attribute per call and is rate limited | `collectors/cloudtrail.py` | Current `LookupEvents` API and quotas |
| Partition is `aws` only. China and GovCloud are out of scope | `ids.py` (D13) | Whether a customer uses another partition |
| Stack resource listings return physical ids, not ARNs, except for the passthrough types below | `ids.py`, stack normaliser | A real `DescribeStackResources` page |
| For ELBv2 load balancers, target groups and listeners, CloudFormation stacks, and SNS topics, the physical id is the ARN. Anything else for those types is left `unresolved:` | `ids.py` | Real stack exports for those types |
| S3 bucket ARNs carry neither region nor account. The key uses region `global` and the snapshot account | `ids.py` | Bucket ARNs in the wild |
| Tag conventions vary by customer. The default workload tag keys are a starting list only | `ruleset.py` | Keys actually used on the accounts in scope |
| `GetResources` lists resources that are or were tagged. A never-tagged resource is absent from the tags snapshot | `collectors/tags.py` | Tagging API behaviour for untagged resources |
| `DescribeStackResources` returns at most 100 resources per stack. A larger stack is truncated and the snapshot should be treated as partial | `collectors/stacks.py` | The service quota, and whether pagination exists |
| Throttling is signalled by a fixed set of error codes. The collector retries those with exponential backoff | `collectors/base.py` | Error codes seen in customer accounts |
| These creation events create the listed resource types. Other entries on the event are references. An event name not in the map treats every listed resource as created | `normalisers/cloudtrail.py` (`CREATED_TYPES`) | A sample of real management events |
| Lambda's create event may be recorded as `CreateFunction20150331` as well as `CreateFunction` | `ruleset.py` | Event names in a real trail |
| A CloudTrail `EventTime` with no timezone is UTC | `normalisers/cloudtrail.py` | Whether the API ever returns a naive time |
| Physical ids that are not ARNs, for types outside the small ARN table, become `unresolved:{type}:{id}` | `ids.py` | Which types show up in the first real snapshot |

`known_aws_service_account_ids` is empty on purpose. It is `# TODO(input-needed):`, not an assumption that AWS owns no accounts.
