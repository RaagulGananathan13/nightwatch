# 0002. DynamoDB provisioned capacity inside the always-free tier

- Status: accepted
- Date: 2026-10-28
- Spec sections: 6, 9, 12

## Context

The spec chooses on-demand capacity and calls it "always free up to 25 GB". The always-free
tier actually covers 25 GB of storage plus **25 read and 25 write capacity units of provisioned
capacity**, per Region and payer account. On-demand requests are billed from the first one. Every
table in the account and Region, including tables from other projects, shares the same allowance.

## Decision

Every table uses **provisioned** mode with fixed capacity and no auto-scaling:

| Table / index | RCU | WCU |
|---|---|---|
| `nightwatch` | 8 | 8 |
| `nightwatch` GSI1 | 2 | 2 |
| `shoplite` | 5 | 5 |
| **Total** | **15** | **15** |

That leaves 10 RCU and 10 WCU of headroom for chaos scenarios and runbooks that raise
capacity. A Terraform check keeps the total at or below 25, and Lambda code uses the SDK's
adaptive retry mode to ride out short throttling.

## Consequences

- $0 for capacity.
- DynamoDB throttling becomes easy to reproduce for the chaos lab: lower ShopLite's write
  capacity, then send load.
- Bursts above the provisioned rate rely on DynamoDB's burst credit (up to 300 seconds of unused
  capacity) and retries, so they're absorbed rather than served instantly.
