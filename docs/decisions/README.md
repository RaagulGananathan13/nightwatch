# Architecture decision records

Each record explains one choice, especially where NightWatch departs from the original
[build spec](../../NightWatch-SPEC.md), and why. Most departures exist to keep the project at a
true $0 per month or to close a gap found while reviewing the spec.

| # | Decision | Status |
|---|---|---|
| [0001](0001-function-urls-instead-of-api-gateway.md) | Lambda Function URLs instead of API Gateway | Accepted |
| [0002](0002-dynamodb-provisioned-capacity.md) | DynamoDB provisioned capacity inside the always-free tier | Accepted |
| [0003](0003-llm-strategy-and-call-budget.md) | Groq by default, evidence-first agent, strict LLM call budget | Accepted |
| [0004](0004-terraform-state-s3-native-locking.md) | Terraform state in S3 with native locking | Accepted |
| [0005](0005-python-313-arm64-minimal-dependencies.md) | Python 3.13 on arm64 with minimal dependencies | Accepted |
| [0006](0006-restricted-deployer-and-permissions-boundary.md) | Restricted deployer user and a permissions boundary | Accepted |
| [0007](0007-free-aws-api-calls-only.md) | Use only free AWS API calls | Accepted |
| [0008](0008-ci-ships-code-terraform-ships-infrastructure.md) | CI ships code, reviewed Terraform ships infrastructure | Accepted |
| [0009](0009-tenant-prefixed-single-table.md) | Every DynamoDB key starts with the tenant | Accepted |
| [0010](0010-one-active-incident-per-service.md) | One active incident per service, with optimistic locking | Accepted |
| [0011](0011-evidence-ledger-guard-and-workflow.md) | Evidence ledger, hallucination guard and the investigation workflow | Accepted |

New records copy the layout of an existing one: Context, Decision, Consequences.
