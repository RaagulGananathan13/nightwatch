# 0021. The cost guardian: cost spikes as incidents, for free

- Status: accepted
- Date: 2026-11-01
- Spec sections: F17 (Phase 8)

## Context

NightWatch must cost nothing, so the first sign of spend matters more than any other cost
signal. The spec suggests either a daily Cost Explorer check or a Budgets alert. Cost Explorer
must be opt-in, because it charges per request. Either way, a cost spike should become an
incident and be investigated like any other.

## Decision

1. **Two free triggers, no Cost Explorer.**
   - **The daily check** runs at 07:30 UTC, after the forecast, or on demand with
     `make cost-check`. It reads free CloudWatch usage data:
     - **Jumps:** each catalogue service's functions (requests, GB-seconds) and log groups
       (bytes). A day at least 3 times its usual day (the median of the week before) is a
       spike. Floors of 1,000 requests, 100 GB-seconds and 10 MB keep small numbers quiet.
     - **Free tiers:** the account's free tiers, projected the same way as the forecast
       ([ADR 0018](0018-predictive-warnings.md)). A month heading past one is a signal.
     - **Capacity:** provisioned DynamoDB capacity across NightWatch's and the watched tables,
       when it goes past the free 25 read or 25 write units.
   - **AWS Budget alerts** carry real spend, measured by AWS. The existing budget's three
     alerts (the first cent, actual spend over $1, a forecast over $1) also go to an SNS topic,
     which starts the same Lambda.

   Cost Explorer would add per-service dollar figures, at $0.01 a request, so it isn't used.
2. **The same path as an alarm.** Each signal becomes an alert on a made-up alarm called
   `cost-guard:<what>`, for example `cost-guard:shoplite-api:requests` or
   `cost-guard:budget:actual`. It goes through the ingest pipeline: it opens or joins the
   service's incident, posts to Discord, starts the investigation and later the postmortem.
   The alert id includes the day, so running the check twice in a morning changes nothing.
3. **Investigated in days, not minutes.** A new read-only tool, `usage_history`, shows:
   - the service's daily usage over two weeks;
   - the day each measure jumped;
   - the Lambda versions published meanwhile, so a jump can be lined up with a deploy.

   A cost incident's plan starts with this tool. It skips the 10-minute log queries and the
   minute-by-minute metrics, which say nothing about a day's usage. The MCP server offers the
   tool too.
4. **An all-clear, like an alarm's.** The next morning's check sends OK for each cost alarm it
   no longer sees, once the incident has had no new alert for 12 hours. The incident then
   resolves and gets its postmortem. A budget alert has no all-clear of its own, so the next
   morning's check sends one.
5. **Who it belongs to.** A spike belongs to the resource's service. An account-wide signal (a
   free tier, a budget alert) goes to the service that used the most of it yesterday.

## Consequences

- Free: a daily Lambda run, a few dozen free API calls and one SNS topic. The topic isn't
  encrypted, because AWS Budgets can't publish under the AWS-managed key and a
  customer-managed key costs $1 a month.
- The deployer may now manage SNS topics named `nightwatch-*`. That is a boundary change,
  applied by re-running `create-deployer.sh` in CloudShell.
- Usage stands in for cost. It catches what costs money on this stack (Lambda, logs,
  capacity), not every AWS service; budget alerts cover the rest, a day late, as billing data
  always is.
- The chaos lab's load tests will look like cost spikes, because they are real usage. Read
  those incidents knowing what ran.
