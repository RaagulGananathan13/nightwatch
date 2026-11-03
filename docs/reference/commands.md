# Commands

Every task runs through `make` from the repository root. On Windows, run it from Git Bash.
`make help` prints the same list.

Commands that call AWS use the `nightwatch` AWS CLI profile and the `us-east-2` region.
Commands marked **AWS** change resources in your account.

## Development

| Command | What it does |
|---|---|
| `make install` | Creates `.venv`, installs NightWatch and the development tools, and enables the git hooks. |
| `make check` | Runs everything CI runs on the Python code: lint, strict type checks and unit tests. |
| `make fmt` | Formats the Python and Terraform code. |
| `make lint` | Lints with ruff and checks formatting with black. |
| `make typecheck` | Runs strict type checks with mypy. |
| `make test` | Runs the unit tests with coverage. Integration tests are opt-in: `pytest -m integration`. |
| `make security` | Runs Bandit on the code and pip-audit on the dependencies. |
| `make clean` | Removes caches and build output. |

## Infrastructure

| Command | What it does |
|---|---|
| `make tf-fmt` | Formats the Terraform files. |
| `make tf-check` | Checks Terraform formatting, then validates every module. Needs no AWS access. |
| `make build` | Builds the Lambda packages into `.build/`: ShopLite, NightWatch and the shared dependency layer. |
| `make bootstrap-plan` | Plans the one-time Terraform state bucket. |
| `make bootstrap-apply` | **AWS.** Applies the reviewed bootstrap plan. |
| `make init` | Runs `terraform init` for the dev environment. Needs `infra/envs/dev/backend.hcl`. |
| `make plan` | Plans the dev environment and saves the plan for review. |
| `make apply` | **AWS.** Applies the reviewed dev plan. |
| `make destroy` | **AWS.** Deletes everything in dev, after asking for confirmation. |

## Operations

| Command | What it does |
|---|---|
| `make cost-check` | Runs the cost guardian's daily check now. It also runs every morning. |
| `make resume` | **AWS.** Switches NightWatch back on after an [emergency stop](../cost-and-safety.md#the-emergency-stop). Check the bill first. |
| `make forecast` | Makes today's forecasts now. They also run every morning. |
| `make coach` | Writes the alert-quality report now. It also runs every Monday. |
| `make mcp-check` | Tests the MCP server setup: assumes its role and asks `whats_going_on`. |
| `make backfill-memory` | **AWS.** Writes postmortems and memories for incidents resolved before incident memory existed. |

## Chat setup

| Command | What it does |
|---|---|
| `make discord-endpoint` | Points the Discord application's button clicks at NightWatch. Run it after `make apply`. |
| `make discord-commands` | Registers the `/nw` slash command in your Discord server. Run it after `make apply`. |

## Connected accounts

| Command | What it does |
|---|---|
| `make connect-id TENANT=<name>` | Makes a tenant's ExternalId for the [connect template](../connect.md). |
| `make connect-check TENANT=<name> ROLE=<role-arn>` | Reads through a connected account's role to confirm it works. |

## Dashboard

| Command | What it does |
|---|---|
| `make dashboard` | Builds the web dashboard into `dashboard/dist`. |
| `make dashboard-check` | Type-checks and tests the dashboard. CI runs this too. |
| `make dashboard-dev` | Runs the dashboard locally against the deployed API. Needs `NIGHTWATCH_URL`. |
| `make dashboard-deploy` | **AWS.** Uploads the built dashboard and refreshes CloudFront. CI does this on merge. |

## Chaos and evaluation

These commands break the ShopLite demo application on purpose, then check NightWatch's
response. Run them only against your own dev environment.

| Command | What it does |
|---|---|
| `make chaos-list` | Lists every chaos scenario and its ground truth. |
| `make chaos NAME=<scenario>` | **AWS.** Runs one scenario, for example `make chaos NAME=lambda-timeout`. |
| `make chaos-bad-deploy` | **AWS.** Deploys a faulty ShopLite build for 4 minutes and checks that the diagnosis names it. |
| `make chaos-error-flag` | **AWS.** Turns on ShopLite's error flag for 4 minutes and checks NightWatch's response. |
| `make chaos-fix` | **AWS.** Deploys a faulty build and keeps it live until you approve NightWatch's rollback. |
| `make chaos-fix-flag` | **AWS.** Turns on the error flag and keeps it on until you approve NightWatch's fix. |
| `make memory-check` | **AWS.** Runs the bad deploy twice. The second diagnosis must cite the first. About 30 minutes. |
| `make eval` | **AWS.** Runs every scenario and scores NightWatch, then writes `eval/results.md`. About 2.5 hours. |
