# Configuration

NightWatch is configured in three places:

| Where | What it holds | In git? |
|---|---|---|
| `infra/envs/dev/terraform.tfvars` | Deployment settings | No, git-ignored. Copy it from `terraform.tfvars.example`. |
| `nightwatch/services.yaml` | The service catalogue: what NightWatch watches, and who may approve fixes | Yes. It contains no secrets. |
| SSM Parameter Store | Secrets, and the kill switch | No. Secrets stay in your AWS account. |

## Deployment settings

Set these in `infra/envs/dev/terraform.tfvars`, then run `make plan` and `make apply`. Only
`budget_alert_email` is required.

| Setting | Default | Description |
|---|---|---|
| `budget_alert_email` | None (required) | Receives budget alerts and the dashboard sign-in. Treated as sensitive by Terraform. |
| `budget_limit_usd` | `1` | The monthly budget, in US dollars |
| `budget_services` | NightWatch's five services | The services the budget counts. An empty list counts the whole account. |
| `region` | `us-east-2` | The AWS region. Other regions are untested. |
| `canary_enabled` | `true` | Sends steady test traffic to ShopLite. With `false`, ShopLite's alarms only fire during chaos tests. |
| `canary_schedule` | `rate(1 minute)` | How often the ShopLite canary runs |
| `dashboard_users` | `[]` | Email addresses that may sign in to the dashboard. Empty means only `budget_alert_email`. |
| `discord_channel_id` | `""` | The Discord channel for approval cards. Empty means no approval buttons in Discord. |
| `discord_public_key` | `""` | The Discord application's public key, used to check every click. Not a secret. |
| `slack_channel_id` | `""` | The Slack channel for incident cards, such as `C0123ABCD`. Empty means Slack is off. |
| `github_repo` | `""` | Your repository as `owner/name`, added to every resource as a tag |
| `github_subject_prefix` | `""` | Your repository's OIDC subject prefix. Empty means no GitHub Actions roles. See [Connect GitHub Actions](../deploy.md#connect-github-actions-optional). |
| `create_github_oidc_provider` | `true` | Set `false` if the account already has a GitHub OIDC provider |

## Service catalogue

`nightwatch/services.yaml` lists each watched service under `services`, keyed by its name. The
name must match the `nightwatch:service` tag on the service's alarms.

| Field | Required | Description |
|---|---|---|
| `description` | No | One line about the service, shown to the investigator |
| `owner` | No | The owning person or team |
| `repository` | No | `owner/name` on GitHub, for recent changes and fix pull requests |
| `lambda_functions` | No | The service's Lambda function names |
| `lambda_alias` | No | The alias that serves traffic. Default `live`. |
| `log_groups` | No | CloudWatch log groups the investigator may read |
| `dynamodb_tables` | No | DynamoDB tables the investigator may describe |
| `ssm_parameters` | No | SSM parameters the investigator may read, such as feature flags. Never secrets. |
| `dependencies` | No | What the service depends on, as `type:name` |
| `deploys` | No | How the service is deployed, in a sentence. Helps the diagnosis. |
| `runbooks` | No | The [runbooks](runbooks.md) NightWatch may propose for this service |
| `approvers` | No | Discord user ids allowed to approve or reject fixes |
| `slack_approvers` | No | Slack member ids allowed to do the same |
| `slo` | No | The availability target in percent, such as `99.5`. Used for the error-budget forecast. |
| `public_name` | No | The name customers know, used on the status page |
| `terraform` | No | Settings a fix pull request may raise. Each has `file`, `block`, `attribute` and `max`. |
| `account` | No | A [connected account](../connect.md) the service runs in. Each has `role_arn`, `external_id_param`, `region` and an optional `llm_key_param`. |

Resources listed here must also be allowed in Terraform's `watched` block and in the
permissions boundary. See [Watch your own service](../watch-your-service.md).

## Secrets and parameters

Store secrets with the commands in the [deploy guide](../deploy.md#4-store-your-secrets), which
ask for the value at a prompt. Terraform knows only these names, never the values.

| Parameter | Type | Created by | Needed for |
|---|---|---|---|
| `/nightwatch/discord/webhook_url` | SecureString | You | Incident cards in Discord |
| `/nightwatch/llm/groq_api_key` | SecureString | You | Diagnosis with Groq |
| `/nightwatch/llm/gemini_api_key` | SecureString | You (optional) | Diagnosis with Gemini |
| `/nightwatch/discord/bot_token` | SecureString | You (optional) | Approval buttons and `/nw` in Discord |
| `/nightwatch/slack/bot_token` | SecureString | You (optional) | Slack cards and commands |
| `/nightwatch/slack/signing_secret` | SecureString | You (optional) | Checking Slack requests |
| `/nightwatch/github/token` | SecureString | You (optional) | Fix pull requests. Use a fine-grained token for one repository, with only Contents and Pull requests write access. |
| `/nightwatch/tenants/<name>/external_id` | String | `make connect-id` | [Connected accounts](../connect.md). Not a secret, but unique to each tenant. |
| `/nightwatch/kill_switch` | String | Terraform | Stops every fix while it is `true` |
| `/nightwatch/dashboard/url`, `/nightwatch/dashboard/client_id` | String | Terraform | The dashboard's sign-in. Not secrets. |

Deployed functions read secrets from SSM at run time, through roles limited to the parameters
each one needs.
