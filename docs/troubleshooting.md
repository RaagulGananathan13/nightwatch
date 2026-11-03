# Troubleshooting

Common problems, what causes them, and how to fix them. If yours isn't here, open an
[issue](https://github.com/RaagulGananathan13/nightwatch/issues). Leave out secrets, account
ids and endpoint URLs.

## Setting up

### `make` isn't found, or fails on Windows

Install it with `winget install ezwinports.make`, open a new terminal, and run `make` from Git
Bash, not PowerShell or Command Prompt.

### `aws` isn't found in Git Bash

AWS CLI version 1 installed with pip often works only in PowerShell on Windows. Run the `aws`
commands in PowerShell, or install AWS CLI version 2.

### A stored secret contains a web page instead of a URL

AWS CLI version 1 downloads any parameter value that starts with `https://`. Run
`aws configure set cli_follow_urlparam false --profile nightwatch`, then store the secret again.

### `make plan` stops at the free-tier guard

The plan would go past the free tier for CloudWatch alarm metrics (10) or DynamoDB capacity
(25 read and 25 write units across all tables). The error message says which. Remove alarms or
capacity until it fits.

### `AccessDenied` during `make apply`

The `NightWatchBoundary` policy in your account is older than the code, or doesn't cover a
resource you added. Pull the latest code in CloudShell and re-run
`bash infra/bootstrap/create-deployer.sh`. See [Upgrade](upgrade-and-teardown.md).

### `Error acquiring the state lock`

An earlier Terraform run was interrupted and left its lock behind. First make sure no other
Terraform command is running. Then remove the lock, using the ID from the error message:

```bash
terraform -chdir=infra/envs/dev force-unlock <lock id>
```

Run `make plan` again afterwards, because a saved plan from before the interruption is out of
date.

## Incidents

### An alarm fired but no incident opened

Check these in order:

1. **The tag.** The alarm needs the `nightwatch:service` tag, and its value must match a service
   in `nightwatch/services.yaml`.
2. **The region.** NightWatch receives alarms from its own region only (`us-east-2`).
3. **The emergency stop.** If chat showed **NightWatch emergency stop**, every function is off.
   See [Day-to-day operation](operations.md#cost-alerts-and-the-emergency-stop).
4. **The logs.** Look in the `/aws/lambda/nightwatch-ingest` log group for errors.

### The diagnosis says it is rule-based

NightWatch couldn't reach a language model. Either no key is stored at
`/nightwatch/llm/groq_api_key` (or `gemini_api_key`), or the day's request budget for the model
is used up. The budget resets each day.

### A second incident joined the first one

That is deliberate. NightWatch keeps one open incident per service, and alarms on the same
service join it ([ADR 0010](decisions/0010-one-active-incident-per-service.md)).

## Chat

### Discord buttons say "This interaction failed"

- The **Interactions Endpoint URL** is out of date. Run `make discord-endpoint` again. This is
  needed whenever the interactions function's URL changes, for example after a teardown.
- `discord_public_key` in `terraform.tfvars` doesn't match the application's public key.

### Approve does nothing, or says I'm not allowed

- Your Discord user id isn't in the service's `approvers` in `nightwatch/services.yaml`, or your
  Slack member id isn't in `slack_approvers`. Add it and run `make plan` and `make apply`.
- The kill switch is on. That stops every fix, and it stays on if `make eval` was stopped part
  way through. See [the kill switch](operations.md#the-kill-switch).

### `make discord-commands` asks for a server id

Pass your Discord server's id: `make discord-commands DISCORD_GUILD_ID=<your server id>`. In
Discord, with Developer Mode on, right-click the server's icon and select **Copy Server ID**.

### A Slack command arrives as a plain message

Type the command instead of pasting it. Slack sends pasted, formatted text as a message.

### Slack shows `dispatch_failed`

The app's request URL or signing secret is wrong. Check that both URLs in the app's settings are
the `discord_interactions_url` output, and store the signing secret again.

## Dashboard and MCP

### No sign-in email arrived

Cognito sends the temporary password to each address in `dashboard_users`, or to
`budget_alert_email`. Check the spam folder. To send it again, remove the address, apply, then
add it back and apply.

### `make mcp-check` fails with `AccessDenied`

Only the IAM users listed in `mcp_users` can switch to the `nightwatch-mcp` role. By default,
that is only the deployer. Run it with the `nightwatch` profile.
