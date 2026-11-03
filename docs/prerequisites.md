# Prerequisites

What you need before you [deploy NightWatch](deploy.md). To only
[try it locally](quickstart.md), you need Git, Python and `make` from the first table, and no
accounts at all.

## Tools

| Tool | Version | Needed for |
|---|---|---|
| Git | Any recent version | Getting the code |
| Python | 3.13 | NightWatch itself, the tests and the scripts |
| GNU make | Any | Every task. On Windows, install it with `winget install ezwinports.make` and run `make` from Git Bash. |
| Terraform | 1.11 or later | Deploying. On Windows: `winget install Hashicorp.Terraform`. |
| AWS CLI | Version 2 recommended | Storing secrets in SSM Parameter Store |
| Node.js | 22 | Only for building the web dashboard |
| GitHub CLI (`gh`) | Any | Only for connecting GitHub Actions |

## Accounts

| Account | Cost | Needed for |
|---|---|---|
| AWS account | Free tier. Measured at about $0.01 a month ([details](cost-and-safety.md)). | Everything. You need to sign in once as the account owner or an administrator. |
| Discord server that you manage | Free | Incident cards. A channel webhook is the minimum. |
| Groq API key | Free | Diagnosis by a language model. Without a key, NightWatch posts a rule-based diagnosis. |
| Google Gemini API key | Free | Optional second language model |
| Slack workspace | Free plan | Optional, alongside or instead of Discord buttons |
| GitHub account | Free | Optional: your own fork, GitHub Actions, and fix pull requests |

## AWS account checks

- **Region.** NightWatch deploys to `us-east-2` (Ohio). Other regions are untested.
- **Shared free tier.** AWS free allowances are per account. NightWatch uses 8 of the 10 free
  CloudWatch alarm metrics, so other projects in the same account share what is left. A guard
  in Terraform stops a plan that would go past the free tier.
- **An email address** for budget alerts and the dashboard sign-in.

## Handling secrets

You will store a few secrets: a Discord webhook, API keys and bot tokens. Follow these rules
throughout:

- **Store secrets only in SSM Parameter Store**, with the commands in this documentation. They
  ask for the value at a prompt, so it doesn't appear on screen or in your shell history.
- **Never put a secret in a file in the repository**, an issue, a pull request or a chat
  message. `terraform.tfvars`, `backend.hcl` and `.env` are git-ignored. Keep them that way.
- **Never share the deployer's access key.** It stays in your AWS CLI profile on one computer.
- **If a secret leaks,** revoke it with the provider first, then store the new value in SSM
  with the same command.
