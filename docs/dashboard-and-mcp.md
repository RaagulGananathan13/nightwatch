# Dashboard, status page and MCP server

Three more ways to see what NightWatch knows. All three are part of the main deployment and
stay within the free tier.

## Web dashboard

The dashboard lists every incident with its diagnosis, the evidence behind each claim, and the
postmortem. It is a read-only React application served by CloudFront from a private S3 bucket,
behind a Cognito sign-in ([ADR 0015](decisions/0015-dashboard.md)). It needs Node.js 22.

1. **Choose who may sign in.** By default only `budget_alert_email` can. To add people, list
   their email addresses in `terraform.tfvars`, then run `make plan` and `make apply`:

    ```hcl
    dashboard_users = ["you@example.com", "teammate@example.com"]
    ```

    Cognito emails each person a temporary password.

2. **Build and upload it:**

    ```bash
    make dashboard dashboard-deploy
    terraform -chdir=infra/envs/dev output -raw dashboard_url
    ```

3. **Sign in** at that address with the temporary password, then choose your own.

To redeploy the dashboard automatically on every merge that changes it, save two more
repository variables (this needs [GitHub Actions](deploy.md#connect-github-actions-optional)):

```bash
gh variable set DASHBOARD_BUCKET          --body "$(terraform -chdir=infra/envs/dev output -raw dashboard_bucket)"
gh variable set DASHBOARD_DISTRIBUTION_ID --body "$(terraform -chdir=infra/envs/dev output -raw dashboard_distribution_id)"
```

## Public status page

The status page tells customers about an incident in plain words. Nothing reaches it until a
person approves it ([ADR 0019](decisions/0019-stakeholder-updates.md)).

1. In chat, type `/nw update <incident>`. NightWatch drafts a customer update and a leadership
   summary, with internal names left out.
2. An approver reviews the draft and selects **Publish to status page**, or **Discard**.
3. The page is at `status/index.html` under your dashboard address. Anyone with the link can read
   it, without signing in.

Each service's customer-facing name is `public_name` in `nightwatch/services.yaml`.

## MCP server

The MCP server gives your own AI assistant, such as Claude Code or Claude Desktop, read-only
access to NightWatch: open incidents, diagnoses, evidence, and the investigator's own tools
([ADR 0020](decisions/0020-mcp-server.md)). It runs on your computer only while the assistant
uses it, so nothing new runs in AWS.

**Before you start:** the server uses your `nightwatch` AWS profile and immediately switches to
the read-only `nightwatch-mcp` role. Only the users listed in `mcp_users` may switch to it. By
default, that is the deployer.

1. **Test the setup:**

    ```bash
    make mcp-check
    ```

    It switches to the role, reads the incident table, writes an audit record and asks
    `whats_going_on`.

2. **Add it to your assistant.** Use the Python in the repository's virtual environment:
   `.venv/Scripts/python.exe` on Windows, or `.venv/bin/python` on macOS and Linux.

    === "Claude Code"

        ```bash
        claude mcp add nightwatch -- /path/to/nightwatch/.venv/bin/python -m nightwatch.mcp_server
        ```

    === "Claude Desktop"

        Add this to the `mcpServers` section of Claude Desktop's configuration file:

        ```json
        {
          "mcpServers": {
            "nightwatch": {
              "command": "/path/to/nightwatch/.venv/bin/python",
              "args": ["-m", "nightwatch.mcp_server"],
              "env": { "AWS_PROFILE": "nightwatch", "AWS_REGION": "us-east-2" }
            }
          }
        }
        ```

3. **Ask** something like "What's going on in NightWatch?".

| Tool | What it returns |
|---|---|
| `whats_going_on` | Open incidents with their checked diagnoses, and today's forecast warnings |
| `list_incidents` | Recent incidents |
| `get_incident` | Every hypothesis, the evidence it cites, and whether the guard verified it |
| `get_evidence` | One piece of evidence: the exact query and its result |
| The investigator's seven tools | Logs, metrics, recent changes and service details, read-only |

Every answer is redacted the same way as evidence sent to a language model, and every call is
written to the audit log under `mcp:<your user>`.
