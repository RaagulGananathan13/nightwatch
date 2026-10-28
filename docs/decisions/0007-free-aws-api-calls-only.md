# 0007. Use only free AWS API calls

- Status: accepted
- Date: 2026-10-28
- Spec sections: 6, 7 (F3, F17), 12

## Context

Some of the obvious AWS APIs for an investigation agent are billed outside the free tier.
Over hundreds of investigations those calls would add up.

## Decision

| Need | Use | Avoid (and why) |
|---|---|---|
| Metric datapoints | `GetMetricStatistics`, which counts toward the 1 million free CloudWatch API requests | `GetMetricData` and `GetMetricWidgetImage`, which are always charged |
| Log search | Logs Insights limited to 1 hour or less on NightWatch-managed log groups, within the 5 GB free "data scanned" allowance | Wide or unbounded queries |
| Recent changes | `cloudtrail:LookupEvents` on Event history, which is free | Creating trails (S3 storage and data events cost money) |
| Cost signals | AWS Budgets alerts, which are free | Cost Explorer ($0.01 per request; opt-in only) |
| Alarms and metrics | 10 alarms or fewer per account, and AWS-provided metrics only | Custom metrics and EMF beyond the 10 free ones |
| Encryption | AWS-managed KMS keys, with SSM values cached to stay under 20,000 free KMS requests a month | Customer-managed keys ($1 a month each) |
| Secrets | SSM Parameter Store, standard tier | Secrets Manager ($0.40 per secret a month) |

Log groups keep logs for 7 days for ShopLite and 14 days for NightWatch.

## Consequences

Some Checkov rules ask for customer-managed KMS keys, point-in-time recovery or S3 access
logging. Those rules are skipped, and each skip states its cost reason in the Terraform code.
