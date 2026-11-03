# Cost and safety

This page describes what NightWatch costs to run, the controls that keep it inside the AWS
free tier, and how the public repository is protected. NightWatch runs entirely in your own AWS
account.

## Measured cost

These figures are measured, not estimated. They are the AWS budget's real spend for the
services NightWatch uses, across the whole account.

| Month | Real spend |
|---|---|
| October 2026 | $0.01 |
| November 2026, to the 3rd | under $0.01 |

NightWatch therefore costs **about one cent a month**, not exactly zero. Everything else stays
inside AWS's always-free allowances, and the LLM runs on Groq's or Gemini's free tier, or on
Ollama on your own machine.

## Cost controls

Four controls work in layers. Each catches what the one before it might miss.

1. **Built to fit the free tiers.** Function URLs instead of API Gateway, provisioned DynamoDB
   capacity, at most 10 alarms. A free-tier guard in Terraform refuses a plan that would go
   past them. See the [README's cost table](https://github.com/RaagulGananathan13/nightwatch#cost).
2. **The cost guardian** checks free CloudWatch usage data every morning. A spike or a free
   tier running out becomes an incident, investigated like any other
   ([ADR 0021](decisions/0021-cost-guardian.md)).
3. **AWS budget alerts** email you, and tell the cost guardian, at the first cent of real
   spend, at $1, and when AWS forecasts more than $1 for the month.
4. **The emergency stop** switches everything off before spend can grow
   ([ADR 0024](decisions/0024-emergency-stop.md)).

## The emergency stop

| What trips it | How fast |
|---|---|
| Real spend reaches **$0.50** this month, about 50 times a normal month | When AWS updates the budget, up to three times a day |
| More than **30,000 Lambda calls in 5 minutes**, such as a flood on a public endpoint. The busiest 5 minutes so far had 5,355. | Within about 5 minutes |
| The morning check finds a free tier already used up, or DynamoDB capacity past the free tier | Each morning |

When it trips, NightWatch:

1. tells Discord and Slack why;
2. turns the kill switch on, so no fix can run;
3. shrinks any DynamoDB table provisioned past the free tier to 1 unit;
4. switches off every NightWatch and ShopLite Lambda, itself last.

A switched-off Lambda refuses every request, and AWS doesn't bill refused requests. Nothing is
deleted. Your incidents, logs and settings stay. The stop only touches functions named
`nightwatch-*` or `shoplite-*`, so anything else in the account keeps running.

!!! warning "NightWatch is off while it is stopped"
    No alerts reach chat until you switch it back on. That is deliberate: a monitor that
    costs money is worse than no monitor.

To switch it back on, check the bill first (AWS console, Billing, Bills), then run:

```bash
make resume    # switches the functions back on and turns the kill switch off
make apply     # only if the stop shrank a table: sets its capacity from Terraform again
```

The stop doesn't act on forecasts, which are often wrong early in a month. Because AWS updates
budgets with a delay, a slow leak could pass $0.50 before the stop runs.

## Public repository and secrets

The code is public. Nothing that gives access to an AWS account is.

| Public on GitHub | Never in the repository |
|---|---|
| The code, Terraform, runbooks and docs | Tokens, webhooks and API keys. They live in SSM Parameter Store in your account. |
| How the permissions are set up | Your AWS account id, endpoint URLs and Terraform state. State is in a private S3 bucket. |
| The tests, with fake clients | The deployer's access key. It stays on the owner's laptop. |

Gitleaks scans every pull request and the whole git history. GitHub's secret scanning and push
protection are on too.

## Contributions and CI

Anyone can fork the repository and open a pull request. This is what a pull request can reach:

- **CI on a fork's pull request has no secrets and no AWS access.** It runs lint, tests and
  security scans. The Terraform plan job runs only for branches in this repository, and only
  the owner can push branches here.
- **Workflows from outside contributors wait for the owner's approval** before they run at
  all.
- **Nothing deploys from a pull request.** Deploys run only from `main`, in the `dev`
  environment, through short-lived OIDC credentials. AWS trusts only this repository, by its
  numeric id, so a copy under the same name can't inherit the access.
- **`main` is protected.** No force pushes and no deletion. Only the owner can merge.
- **Every role is boxed in** by the NightWatch permissions boundary. No role can create IAM
  users or remove the boundary, and Lambda functions, tables and buckets are limited to
  NightWatch's and ShopLite's own.

Common attacks, and why each fails:

| Attempt | Why it fails |
|---|---|
| A pull request that changes a workflow to print secrets | Fork workflows get no secrets, and they wait for approval. |
| Hiding a backdoor in a pull request | It reaches AWS only if the owner merges it. Review changes to `.github/`, `infra/`, `runbooks/` and `nightwatch/remediation/` with extra care. |
| Flooding a public endpoint to run up the bill | Signature checks refuse forged requests in milliseconds. Past 30,000 calls in 5 minutes, the emergency stop switches everything off, and refused calls cost nothing. |
| Tricking the AI with text in a log line | The investigator only reads. A fix needs a reviewed runbook and a person's **Approve**. See the [security review](security-review.md). |

## Self-hosted deployments

Your copy runs entirely in your own AWS account, with your own secrets and your own budget.
Nothing connects back to this repository or its owner. Set the budget's email address before
your first `make apply`, and keep the emergency stop on.

To report a security problem privately, see the
[security policy](https://github.com/RaagulGananathan13/nightwatch/blob/main/SECURITY.md).
