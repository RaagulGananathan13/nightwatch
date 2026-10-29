# Runbooks

Each YAML file is one fix NightWatch may run **after a human approves it** in Discord (spec F5).
`runbook.schema.json` is generated from the code that validates these files
(`python -m nightwatch.remediation.runbooks`), so editors can check them as you type.

| Runbook | Fixes | Changes |
|---|---|---|
| `rollback-lambda-alias` | a bad deploy | moves the `live` alias back to the version that was live before it |
| `reset-feature-flags` | a bad flag change | sets the flags parameter back to `{}` |
| `reset-lambda-concurrency` | a function throttled by a reserved concurrency of 0 | removes the reservation |
| `raise-dynamodb-capacity` | a throttled table | restores the free-tier baseline capacity |

Every runbook has these guarantees:
- **Allow-listed calls only.** It makes one of four allow-listed AWS calls, and the remediator's
  IAM role permits exactly those, only on the watched service's resources.
- **Trusted parameters only.** Parameters come from AWS API data, current resource state or the
  service catalogue, never from LLM text, so a log line can't aim a fix.
- **Verified and reversible.** After running, a metric must stay at or below a threshold for
  2 minutes, and each runbook records how to undo it.
- **Human approval by default.** `auto_approve: true` is only allowed on low-risk runbooks, and
  none of these use it.

The kill switch (`/nightwatch/kill_switch` in SSM set to `true`) disables all remediation.
