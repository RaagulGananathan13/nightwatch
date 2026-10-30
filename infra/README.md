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

## First-time setup

Run the `make` commands from Git Bash in the repository root.

1. **Create the deployer (once, as the account owner).** Open [AWS CloudShell](https://us-east-2.console.aws.amazon.com/cloudshell/home?region=us-east-2) and run:

   ```bash
   git clone https://github.com/RaagulGananathan13/nightwatch.git
   bash nightwatch/infra/bootstrap/create-deployer.sh
   ```

   Paste the three `aws configure set ... --profile nightwatch` lines it prints into a terminal on
   your laptop, then clear the CloudShell screen.

2. **Create the state bucket.**

   ```bash
   make bootstrap-plan      # read the plan: one S3 bucket and its settings
   make bootstrap-apply
   terraform -chdir=infra/bootstrap output -raw backend_config > infra/envs/dev/backend.hcl
   ```

3. **Store the Discord webhook (Phase 2).** In Discord, open your alerts channel and go to
   **Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL**. Then run this in
   PowerShell exactly as written, and paste the URL when it asks:

   ```powershell
   aws configure set cli_follow_urlparam false --profile nightwatch
   $url = Read-Host "Paste the Discord webhook URL"; aws ssm put-parameter --profile nightwatch --name /nightwatch/discord/webhook_url --type SecureString --value $url --overwrite; Remove-Variable url
   ```

   The first line stops AWS CLI v1 from downloading `https://` values instead of storing them.
   Terraform never sees the URL.

   For Phase 3, store a free Groq API key from [console.groq.com/keys](https://console.groq.com/keys) the same way:

   ```powershell
   $key = Read-Host "Paste the Groq API key"; aws ssm put-parameter --profile nightwatch --name /nightwatch/llm/groq_api_key --type SecureString --value $key --overwrite; Remove-Variable key
   ```

   A Gemini key at `/nightwatch/llm/gemini_api_key` is optional. Without any key, NightWatch still
   posts a rule-based diagnosis.

4. **Fill in your settings.** Copy `envs/dev/terraform.tfvars.example` to `envs/dev/terraform.tfvars`
   and set `budget_alert_email`.

5. **Build the environment.**

   ```bash
   make init
   make plan                # read it; nothing is created yet
   make apply
   ```

6. **Connect GitHub Actions.** Save the outputs as repository settings:

   ```bash
   gh variable set AWS_DEPLOY_ROLE_ARN --body "$(terraform -chdir=infra/envs/dev output -raw github_deploy_role_arn)"
   gh variable set AWS_PLAN_ROLE_ARN   --body "$(terraform -chdir=infra/envs/dev output -raw github_plan_role_arn)"
   gh variable set SHOPLITE_URL        --body "$(terraform -chdir=infra/envs/dev output -raw shoplite_url)"
   gh variable set TF_STATE_BUCKET     --body "$(terraform -chdir=infra/bootstrap output -raw state_bucket)"
   gh secret set BUDGET_ALERT_EMAIL
   ```

   Then create the `dev` environment under **Settings → Environments** and limit it to the `main`
   branch. The deploy role only trusts jobs in that environment.
   If you forked the repo, first set `github_subject_prefix` in `terraform.tfvars` to your repo's value,
   from `gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix`. GitHub signs
   tokens with immutable owner and repo IDs, and the roles trust only that exact subject.

7. **Turn on approval buttons (Phase 4).** Approval cards are posted by a Discord bot, because
   webhook messages can't carry buttons. In the [Developer Portal](https://discord.com/developers/applications),
   create an application, then go to **Bot → Reset Token** and add the bot to your server with the
   **Send Messages** permission. Store the token the same way as the webhook:

   ```powershell
   $token = Read-Host "Paste the bot token"; aws ssm put-parameter --profile nightwatch --name /nightwatch/discord/bot_token --type SecureString --value $token --overwrite; Remove-Variable token
   ```

   Then complete these settings:
   - In `terraform.tfvars`, set `discord_channel_id` (your #incidents channel) and
     `discord_public_key` (on the application's **General Information** page).
   - In `nightwatch/services.yaml`, list your Discord user id under `approvers`.

   After `make apply`, run `make discord-endpoint`. It saves the interactions Lambda's URL as the
   application's **Interactions Endpoint URL**, and Discord tests the URL before accepting it.
   To stop every fix at any time:

   ```bash
   aws ssm put-parameter --profile nightwatch --name /nightwatch/kill_switch --value true --overwrite
   ```

8. **Turn on slash commands and memory (Phase 6).** After `make apply`:

   ```bash
   make discord-commands DISCORD_GUILD_ID=<your server id>   # registers /nw in your server
   make backfill-memory                                      # postmortems for older incidents
   ```

9. **Open the dashboard (Phase 7).** The deployer needs CloudFront and Cognito rights, so first
   re-run the setup script in CloudShell: `cd nightwatch && git pull` then
   `bash infra/bootstrap/create-deployer.sh`. Run it again whenever
   `nightwatch-boundary-policy.json` changes. Then:

   ```bash
   make apply                    # Cognito emails a temporary password to budget_alert_email
   make dashboard dashboard-deploy
   terraform -chdir=infra/envs/dev output -raw dashboard_url
   gh variable set DASHBOARD_BUCKET          --body "$(terraform -chdir=infra/envs/dev output -raw dashboard_bucket)"
   gh variable set DASHBOARD_DISTRIBUTION_ID --body "$(terraform -chdir=infra/envs/dev output -raw dashboard_distribution_id)"
   ```

   After that, CI redeploys the dashboard on every merge that changes it. To let more people
   sign in, list their emails in `dashboard_users` in `terraform.tfvars`.

10. **Ship code.** Any merge to `main` that touches `shoplite/` now deploys a new version. You can also
   start one by hand with **Actions → Deploy ShopLite → Run workflow**.

## Tearing down

`make destroy` removes everything in `envs/dev`. The state bucket and the deployer user stay, so you
can rebuild with `make plan` and `make apply`. To remove those as well, empty and delete the bucket, then
delete the `nightwatch-deployer` user and the `NightWatchBoundary` policy in the IAM console.
