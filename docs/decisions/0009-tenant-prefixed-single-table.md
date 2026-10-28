# 0009. Every DynamoDB key starts with the tenant

- Status: accepted
- Date: 2026-10-28
- Spec sections: 9, 13

## Context

Section 13 of the spec isolates tenants with IAM's `dynamodb:LeadingKeys` condition, which
only works if **every** partition key starts with `TENANT#<id>`. Section 9 breaks that rule in
two ways:

- Alerts, evidence and actions are partitioned by `INC#<ulid>`.
- GSI1 is keyed on a bare `status`, which mixes all tenants in one index partition.

## Decision

All keys carry the tenant, including index keys:

| Item | PK | SK |
|---|---|---|
| Incident | `TENANT#<t>` | `INC#<ulid>` |
| Service pointer (correlation lock, ADR 0010) | `TENANT#<t>` | `OPEN#<service>` |
| Counter for INC-### numbers | `TENANT#<t>` | `META` |
| Audit entry | `TENANT#<t>` | `AUDIT#<time>#<ulid>` |
| Alert, evidence, action | `TENANT#<t>#INC#<ulid>` | `ALERT#…` / `EV#…` / `ACT#…` |

GSI1 lists incidents by status: `GSI1PK = TENANT#<t>#STATUS#<status>` and
`GSI1SK = <opened_at>#<ulid>`, newest first. Raw alerts get a 90-day TTL. Incidents and
audit entries never expire.

## Consequences

- Multi-tenancy later is an IAM condition, not a data migration.
- One tenant's incidents and audit entries share a partition. That's fine at small scale, and
  incident activity is bursty rather than constant.
- Single-tenant v1 uses the tenant id `default`.
