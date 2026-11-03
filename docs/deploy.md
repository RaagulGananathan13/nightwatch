# Deploy to AWS

This guide deploys NightWatch, the ShopLite demo application and its alarms, and a monthly
budget into your own AWS account. When you finish, an alarm on ShopLite becomes an incident with
a diagnosis in your Discord channel. Allow about 30 minutes, most of it waiting for AWS.

Before you start, check the [prerequisites](prerequisites.md). Run `make` commands from the
repository root. On Windows, use Git Bash for `make` and PowerShell for the commands that store
secrets.

## 1. Get the code

Fork the repository on GitHub if you want GitHub Actions or fix pull requests for your copy.
Otherwise, clone it directly.

```bash
git clone https://github.com/<your-account>/nightwatch.git
cd nightwatch
make install
```

## 2. Create the deployer

Every NightWatch deployment runs as a restricted IAM user, `nightwatch-deployer`. Its policy, the
`NightWatchBoundary`, is also the permissions boundary of every role NightWatch creates. Nothing
NightWatch deploys can ever do more than that policy allows
([ADR 0006](decisions/0006-restricted-deployer-and-permissions-boundary.md)).

1. Sign in to the AWS console as the account owner or an administrator, and open
   [CloudShell](https://us-east-2.console.aws.amazon.com/cloudshell/home?region=us-east-2) in
   `us-east-2`.
2. Run the setup script:

    ```bash
    git clone https://github.com/RaagulGananathan13/nightwatch.git
    bash nightwatch/infra/bootstrap/create-deployer.sh
    ```

3. The script prints three `aws configure set` lines with a new access key. **Don't paste them
   into a terminal that keeps history.** Instead, run this on your computer and enter each value
   when it asks:

    ```bash
    aws configure --profile nightwatch
    ```

    Enter the access key ID, the secret access key and `us-east-2`. Leave the output format
    empty.

4. Clear the CloudShell screen with `clear`, then close CloudShell.

If the script says the account already has a GitHub OIDC provider, add
`create_github_oidc_provider = false` to your settings in step 5.

## 3. Create the state bucket

Terraform keeps its state in a private, versioned S3 bucket.

```bash
make bootstrap-plan      # read the plan: one S3 bucket and its settings
make bootstrap-apply
terraform -chdir=infra/bootstrap output -raw backend_config > infra/envs/dev/backend.hcl
```

## 4. Store your secrets

If you use AWS CLI version 1, run this once first. Without it, version 1 downloads any value
that starts with `https://` instead of storing it.

```bash
aws configure set cli_follow_urlparam false --profile nightwatch
```

**Discord webhook (required).** In Discord, open your alerts channel and go to
**Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL**. Then store it:

=== "PowerShell"

    ```powershell
    $url = Read-Host "Paste the Discord webhook URL"; aws ssm put-parameter --profile nightwatch --name /nightwatch/discord/webhook_url --type SecureString --value $url --overwrite; Remove-Variable url
    ```

=== "bash (macOS, Linux)"

    ```bash
    read -rsp "Paste the Discord webhook URL: " url; echo; aws ssm put-parameter --profile nightwatch --name /nightwatch/discord/webhook_url --type SecureString --value "$url" --overwrite; unset url
    ```

**Groq API key (recommended).** Create a free key at
[console.groq.com/keys](https://console.groq.com/keys), then store it the same way:

=== "PowerShell"

    ```powershell
    $key = Read-Host "Paste the Groq API key"; aws ssm put-parameter --profile nightwatch --name /nightwatch/llm/groq_api_key --type SecureString --value $key --overwrite; Remove-Variable key
    ```

=== "bash (macOS, Linux)"

    ```bash
    read -rsp "Paste the Groq API key: " key; echo; aws ssm put-parameter --profile nightwatch --name /nightwatch/llm/groq_api_key --type SecureString --value "$key" --overwrite; unset key
    ```

A Google Gemini key is optional. Store it as `/nightwatch/llm/gemini_api_key`. Without any
key, NightWatch still posts a rule-based diagnosis.

Terraform never reads these values. It only knows the parameter names, so secrets never reach
Terraform state, outputs or logs.

## 5. Fill in your settings

```bash
cp infra/envs/dev/terraform.tfvars.example infra/envs/dev/terraform.tfvars
```

Set `budget_alert_email` in `infra/envs/dev/terraform.tfvars`. This address gets budget alerts
and the dashboard sign-in. The file is git-ignored. Every other setting is optional and listed
in the [configuration reference](reference/configuration.md).

## 6. Review the service catalogue

`nightwatch/services.yaml` describes the services NightWatch watches. The `shoplite` entry is
the demo application, and it works as it is. Before you deploy, make two changes:

- Set `owner` and `repository` to your GitHub account and fork.
- Replace the example ids in `approvers` and `slack_approvers` with `[]`. You add your own ids
  when you [set up approval buttons](chat.md).

## 7. Deploy

```bash
make init
make plan       # builds the Lambda packages, then saves a plan. Read it: nothing is created yet.
make apply      # applies exactly the plan you read
```

`make plan` stops with an error if the deployment would go past the free tier for DynamoDB
capacity or CloudWatch alarm metrics.

## 8. Check that it works

Break ShopLite on purpose and watch NightWatch respond:

```bash
make chaos NAME=error-flag
```

The scenario turns on a feature flag that makes most requests fail, and sends traffic. Within a
few minutes, your Discord channel shows an incident, then its diagnosis naming the flag. After
4 minutes the scenario turns the flag off and prints a verdict. See
[chaos testing](chaos.md) for the other scenarios.

## Connect GitHub Actions (optional)

With GitHub Actions connected, every merge to `main` that changes `shoplite/` deploys a new
ShopLite version, and pull requests in your repository get a Terraform plan. The roles use
short-lived OIDC credentials, so GitHub never stores an AWS key.

1. Find your repository's OIDC subject prefix:

    ```bash
    gh api repos/<your-account>/nightwatch/actions/oidc/customization/sub --jq .sub_claim_prefix
    ```

2. In `terraform.tfvars`, set `github_repo` to `<your-account>/nightwatch` and
   `github_subject_prefix` to the value from step 1. AWS then trusts only your repository, by its
   numeric id.
3. Run `make plan` and `make apply`. This creates the `github-deploy` and `github-plan` roles.
4. Save the outputs as repository settings:

    ```bash
    gh variable set AWS_DEPLOY_ROLE_ARN --body "$(terraform -chdir=infra/envs/dev output -raw github_deploy_role_arn)"
    gh variable set AWS_PLAN_ROLE_ARN   --body "$(terraform -chdir=infra/envs/dev output -raw github_plan_role_arn)"
    gh variable set SHOPLITE_URL        --body "$(terraform -chdir=infra/envs/dev output -raw shoplite_url)"
    gh variable set TF_STATE_BUCKET     --body "$(terraform -chdir=infra/bootstrap output -raw state_bucket)"
    gh secret set BUDGET_ALERT_EMAIL
    ```

    `gh secret set` asks for the value, so it doesn't appear in your shell history.

5. In GitHub, create the `dev` environment under **Settings → Environments** and limit it to the
   `main` branch. The deploy role trusts only jobs in that environment.

## Next steps

- [Set up Discord and Slack](chat.md): approval buttons, `/nw` commands and Slack.
- [Dashboard and MCP server](dashboard-and-mcp.md): the web dashboard, the public status page
  and your own AI assistant.
- [Watch your own service](watch-your-service.md) instead of, or as well as, ShopLite.
- [Day-to-day operation](operations.md): the kill switch, the emergency stop and the cost
  checks.
