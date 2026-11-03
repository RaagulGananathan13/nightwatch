# Infrastructure (Terraform)

Everything NightWatch runs on is created by Terraform in **us-east-2** and removed by
`make destroy`. Nothing here leaves the AWS always-free tier. `terraform plan` stops with an error if
DynamoDB capacity or CloudWatch alarm metrics would go over it.

| Folder | Purpose |
|---|---|
| `bootstrap/` | One-time setup: the `nightwatch-deployer` CloudShell script, its permission policy, and the S3 bucket for Terraform state |
| `modules/budget` | $1 monthly budget on NightWatch's services (Lambda, DynamoDB, Step Functions, CloudWatch, S3), emailing at the first cent, at $1, and when the forecast passes $1 |
| `modules/iam` | GitHub OIDC trust plus two roles: `github-deploy` (ships code) and `github-plan` (read-only) |
| `modules/shoplite` | The demo store: DynamoDB table, Lambda with `live` alias and IAM-auth URL, flags parameter, canary |
| `modules/alarms` | The five ShopLite alarms (7 of the 10 free alarm metrics), each tagged with its service and severity |
| `modules/nightwatch-core` | The `nightwatch` table, the dependency layer, the `nightwatch-ingest` Lambda with its dead-letter queue and EventBridge rule, and the incident workflow: the `nightwatch-incident` state machine, the read-only `nightwatch-investigate` Lambda and `nightwatch-report` |
| `envs/dev/` | Wires the modules together and checks the free-tier limits |

Every IAM role lives under `/nightwatch/` and carries the `NightWatchBoundary` permissions boundary, so
nothing Terraform creates can do more than that policy allows ([ADR 0006](../docs/decisions/0006-restricted-deployer-and-permissions-boundary.md)).

## Deploying

The step-by-step guides are on the documentation site, so they live in one place:

| Guide | Covers |
|---|---|
| [Prerequisites](https://raagulgananathan13.github.io/nightwatch/prerequisites/) | Tools, accounts, and how to handle secrets |
| [Deploy to AWS](https://raagulgananathan13.github.io/nightwatch/deploy/) | The deployer, the state bucket, secrets, settings, the first deploy, GitHub Actions |
| [Set up Discord and Slack](https://raagulgananathan13.github.io/nightwatch/chat/) | Approval buttons, `/nw` commands, Slack |
| [Dashboard, status page and MCP server](https://raagulgananathan13.github.io/nightwatch/dashboard-and-mcp/) | The web dashboard and your own AI assistant |
| [Watch your own service](https://raagulgananathan13.github.io/nightwatch/watch-your-service/) | The boundary, the catalogue, `watched` and alarms |
| [Upgrade and tear down](https://raagulgananathan13.github.io/nightwatch/upgrade-and-teardown/) | New versions, the boundary policy, `make destroy` |
| [Configuration](https://raagulgananathan13.github.io/nightwatch/reference/configuration/) | Every setting in `terraform.tfvars`, and every SSM parameter |
| [Troubleshooting](https://raagulgananathan13.github.io/nightwatch/troubleshooting/) | State locks, `AccessDenied`, missing incidents, chat problems |

`terraform.tfvars` and `backend.hcl` are git-ignored. Secrets never go in either: they live in
SSM Parameter Store, and Terraform only knows their names.
