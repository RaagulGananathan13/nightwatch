# Runbooks

A runbook is one reviewed fix that NightWatch may propose. Each is a YAML file in `runbooks/`,
validated against `runbooks/runbook.schema.json`. A runbook runs only for services that list it
in `nightwatch/services.yaml`, and only after an approver selects **Approve**
([ADR 0012](../decisions/0012-approval-gated-remediation.md)).

| Runbook | Fixes | What it changes | AWS call |
|---|---|---|---|
| `rollback-lambda-alias` | A bad deploy | Moves the function's alias back to the version that was live before the latest deploy | `lambda:UpdateAlias` |
| `reset-feature-flags` | A bad flag change | Sets the service's flags parameter back to `{}` | `ssm:PutParameter` |
| `reset-lambda-concurrency` | A function throttled by a reserved concurrency of 0 | Removes the reservation | `lambda:DeleteFunctionConcurrency` |
| `raise-dynamodb-capacity` | A throttled table | Restores the table's free-tier baseline capacity | `dynamodb:UpdateTable` |

## Guarantees

Every runbook keeps these rules, and the code enforces them:

- **Four calls only.** A runbook can use only the four AWS calls above. The remediator's IAM role
  allows exactly those, and only on the watched service's resources.
- **Trusted parameters only.** Parameters come from AWS API data, the resource's current state
  or the service catalogue. They never come from log text or the model's answer, so a log line
  can't aim a fix.
- **Verified and reversible.** After a fix runs, a metric must stay at or below its threshold
  for 2 minutes. If it gets worse, the fix is undone automatically. Each runbook records how to
  undo it.
- **A person approves.** `auto_approve: true` is allowed only on low-risk runbooks, and none of
  these four use it.
- **The kill switch wins.** While `/nightwatch/kill_switch` is `true`, no runbook runs.

## What a service needs for each runbook

| Runbook | Requirement |
|---|---|
| `rollback-lambda-alias` | Traffic reaches the function through an alias, `lambda_alias` in the catalogue, that points at a published version |
| `reset-feature-flags` | The service keeps its feature flags as JSON in one SSM parameter, listed in `ssm_parameters` |
| `reset-lambda-concurrency` | None |
| `raise-dynamodb-capacity` | The table uses provisioned capacity mode |
