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
| `modules/alarms` | The five ShopLite alarms (7 of the 10 free alarm metrics) |
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

3. **Fill in your settings.** Copy `envs/dev/terraform.tfvars.example` to `envs/dev/terraform.tfvars`
   and set `budget_alert_email`.

4. **Build the environment.**

   ```bash
   make init
   make plan                # read it; nothing is created yet
   make apply
   ```

5. **Connect GitHub Actions.** Save the outputs as repository settings:

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

6. **Ship code.** Any merge to `main` that touches `shoplite/` now deploys a new version. You can also
   start one by hand with **Actions → Deploy ShopLite → Run workflow**.

## Tearing down

`make destroy` removes everything in `envs/dev`. The state bucket and the deployer user stay, so you
can rebuild with `make plan` and `make apply`. To remove those as well, empty and delete the bucket, then
delete the `nightwatch-deployer` user and the `NightWatchBoundary` policy in the IAM console.
