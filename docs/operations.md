# Day-to-day operation

What NightWatch does on its own, and the controls you use while it runs.

## What happens to an alarm

1. **Incident.** A tagged CloudWatch alarm fires. NightWatch opens an incident for the service,
   or adds the alarm to the service's open incident, and posts a card in chat.
2. **Investigation.** The investigator gathers evidence with read-only tools: logs, metrics,
   recent deploys and changes, and similar past incidents. A language model proposes the root
   cause, and the guard checks every fact it cites. The card is updated with the diagnosis.
3. **Fix.** If a runbook fits, an approval card asks the service's approvers. Nothing runs until
   an approver selects **Approve**.
4. **Verification.** After the fix, NightWatch watches the alarm's metrics for 2 minutes. If they
   get worse, it undoes the fix automatically.
5. **Postmortem.** When the alarms recover, the incident resolves and NightWatch writes a
   blameless postmortem. It is stored in S3, shown in the dashboard and kept in incident memory.

## Scheduled reports

| When | Report | Where it goes |
|---|---|---|
| Every morning | **Forecasts**: free-tier usage, each service's error budget, Lambda concurrency and table read headroom, each against its limit | Dashboard every day. Chat only when a forecast gets worse. |
| Every morning | **Cost check**: usage spikes, free tiers running out, and DynamoDB capacity | An incident for each signal, like an alarm |
| Every Monday | **Alert-quality report**: a noise score for each alarm, with tuning suggestions | The dashboard's Alarms page, and a digest in chat |

Run any of them now with `make forecast`, `make cost-check` or `make coach`.

## The kill switch

The kill switch stops every fix. Incidents, diagnoses and reports carry on. If NightWatch can't
read the switch, it treats it as on.

=== "Turn it on"

    ```bash
    aws ssm put-parameter --profile nightwatch --name /nightwatch/kill_switch --value true --overwrite
    ```

=== "Turn it off"

    ```bash
    aws ssm put-parameter --profile nightwatch --name /nightwatch/kill_switch --value false --overwrite
    ```

Turn it on while you work on a service by hand, so NightWatch doesn't propose fixes for problems
you're causing on purpose.

## Cost alerts and the emergency stop

An AWS budget emails `budget_alert_email` at the first cent of real spend, at $1, and when AWS
forecasts more than $1 for the month.

The **emergency stop** switches every NightWatch and ShopLite function off when:

- real spend reaches $0.50 in a month;
- Lambda calls exceed 30,000 in 5 minutes;
- the morning check finds a free tier already used up.

Chat is told first. Everything stays off until a person switches it back on.

When chat shows **NightWatch emergency stop**:

1. Open **Billing → Bills** in the AWS console and find what cost money.
2. Fix the cause. For example, remove whatever flooded a public endpoint.
3. Switch NightWatch back on:

    ```bash
    make resume    # switches the functions back on and turns the kill switch off
    make plan      # if the stop shrank a table, the plan shows its capacity being restored
    make apply
    ```

See [Cost and safety](cost-and-safety.md) for how each control works.

## Where to look when something seems wrong

| Question | Where to look |
|---|---|
| Is NightWatch running? | `/nw status` in chat, or the dashboard |
| Why did an incident not open? | The `nightwatch-ingest` function's logs in CloudWatch Logs |
| What did NightWatch do, and who approved it? | The incident in the dashboard, which shows the evidence and each step |
| Is anything costing money? | The budget emails, and `make cost-check` |

Each function logs to `/aws/lambda/<function name>`, for example `/aws/lambda/nightwatch-ingest`.
Logs are kept for a short time to stay within the free tier. The
[troubleshooting](troubleshooting.md) page covers the common problems.
