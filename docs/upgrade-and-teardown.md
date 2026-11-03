# Upgrade and tear down

## Upgrade to a new version

Read the [changelog](changelog.md) first. Then:

1. **Get the new code.** In your clone, `git pull`. If you forked, sync your fork with the
   upstream repository first.
2. **Check the permissions boundary.** If `infra/bootstrap/nightwatch-boundary-policy.json`
   changed, update it before you deploy. In CloudShell, run:

    ```bash
    cd nightwatch && git pull
    bash infra/bootstrap/create-deployer.sh
    ```

    The script updates the policy in place and leaves your access key alone. Skipping this step
    causes `AccessDenied` errors during `make apply`.

3. **Check, plan and apply:**

    ```bash
    make install    # picks up new dependencies
    make check
    make plan       # read it, especially anything it replaces or destroys
    make apply
    ```

4. If the dashboard changed, run `make dashboard dashboard-deploy`, unless GitHub Actions does
   it for you.

## Tear down

`make destroy` deletes everything Terraform created in the dev environment, after asking you to
confirm. That includes the functions, the tables and their incidents, the dashboard, the alarms
and the budget.

```bash
make destroy
```

These stay after `make destroy`, so that you can deploy again with `make plan` and `make apply`.
Remove them by hand if you are finished with NightWatch:

| What | How to remove it |
|---|---|
| Secrets you stored in SSM | `aws ssm delete-parameters --profile nightwatch --names /nightwatch/discord/webhook_url /nightwatch/llm/groq_api_key` and any others you added. The [configuration reference](reference/configuration.md#secrets-and-parameters) lists them all. |
| The Terraform state bucket | Empty it, then delete it, in the S3 console |
| The deployer user and the boundary policy | Delete the `nightwatch-deployer` user, then the `NightWatchBoundary` policy, in the IAM console |
| The Discord and Slack applications | Delete them in the Discord Developer Portal and on api.slack.com/apps |
| Your local AWS profile | Remove the `[nightwatch]` sections from `~/.aws/credentials` and `~/.aws/config` |

Revoke any API keys you no longer use with their providers, for example Groq.
