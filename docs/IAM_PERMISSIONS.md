# IAM permissions for the cost layer

Documentation only. Nothing here is deployed. The role `TamReadOnly` currently carries only the graph collectors' actions (D49); these must be added before a live cost review can run through it.

`# ASSUMPTION:` the action names below follow the API operation names. Verify each against the IAM service authorisation reference before adding it.

## Cost Explorer (`ce`)

| Action | Used for |
|---|---|
| `ce:GetCostAndUsage` | spend by service and account, window totals, spend by tag |
| `ce:GetCostAndUsageWithResources` | daily resource costs (needs resource-level data enabled) |
| `ce:GetTags` | tag keys that have cost data |

`ce:GetCostForecast` is not needed: the forecast step (F18) is not built.

## Cost Optimization Hub (`cost-optimization-hub`)

| Action | Used for |
|---|---|
| `cost-optimization-hub:ListEnrollmentStatuses` | whether the account is opted in |
| `cost-optimization-hub:ListRecommendations` | recommendations for the accounts in scope |
| `cost-optimization-hub:ListRecommendationSummaries` | the deduplicated savings total per account |
| `cost-optimization-hub:GetRecommendation` | reserved; not called today |
| `cost-optimization-hub:GetPreferences` | reserved; not called today |

Never grant `UpdateEnrollmentStatus` or `UpdatePreferences`. The collectors refuse both before any request is made.

## Policy sketch

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CostRead",
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:GetCostAndUsageWithResources",
        "ce:GetTags",
        "cost-optimization-hub:ListEnrollmentStatuses",
        "cost-optimization-hub:ListRecommendations",
        "cost-optimization-hub:ListRecommendationSummaries",
        "cost-optimization-hub:GetRecommendation",
        "cost-optimization-hub:GetPreferences"
      ],
      "Resource": "*"
    }
  ]
}
```

Cost Explorer charges per paginated request. The collector caps calls per review (`max_calls`, default 40) and the snapshot cache keeps results for 24 hours.
