# Security review (Phase 9)

A check of NightWatch against the spec's security rules (section 11) and quality rules
(section 16), on 2026-11-02, at v0.9 plus this review's fixes. Each row says how the rule is
met and where to check it.

## Section 11: security

| Rule | How NightWatch meets it | Where to check |
|---|---|---|
| Investigator is read-only | Its role reads CloudWatch logs and metrics, CloudTrail and alarms, and only the watched service's functions, tables and parameters. Its only writes go to NightWatch's own table. | `infra/modules/nightwatch-core/investigation.tf` (`read_only_tools`) |
| Remediator runs only runbook calls | Four API calls (`UpdateAlias`, `DeleteFunctionConcurrency`, `PutParameter`, `UpdateTable`), each limited to the exact ARNs of the watched resources. Runbooks may use only these calls. | `remediation.tf`, `nightwatch/remediation/calls.py` (`ALLOWED_CALLS`) |
| CI uses OIDC, with no long-lived keys | GitHub Actions assume roles through OIDC, trusted only for this repository's subject. | `infra/modules/iam` |
| Tool output is untrusted | Logs reach the model inside `<log>` tags, with closing tags defused. The agent has no write tools; an unknown tool is refused. Arguments are checked against the service catalogue. | `test_injected_log_text_cannot_run_tools_or_escape_its_tags` |
| A fix comes only from an approved runbook | The planner fills parameters only from structured evidence (the Lambda API, the catalogue), never from log text or the model's words. A person must click **Approve**. | `test_injected_text_can_not_change_the_fix` (new), `test_approvals.py` |
| PII redaction before an external LLM | Emails, phone numbers, IP addresses, cards (Luhn), AWS keys, JWTs, API keys, GitHub tokens, Discord webhooks and Slack tokens and webhooks (new) are redacted. An allow-list (new) keeps addresses that identify no one: loopback, `0.0.0.0` and the metadata service. The MCP server redacts its answers the same way. | `nightwatch/agent/guard.py`, `test_agent_guard.py` |
| Zero data egress option | The `ollama:` provider runs a local model through Ollama's OpenAI-compatible API. | `nightwatch/agent/providers/openai_compat.py` |
| Chat requests are signed | Discord requests need a valid Ed25519 signature. Slack requests need a valid HMAC-SHA256 signature. Both are refused after 5 minutes. | `test_approvals.py`, `test_slack.py` |
| Only approvers decide | Each service lists its Discord and Slack approvers. Every click and command is checked against them, and refusals are audited. | `ServiceInfo.can_approve` |
| Secrets live in SSM only | Tokens and webhooks are SecureStrings that the owner stores by hand. Terraform only names them, so they never reach state or outputs. Chat clients never put URLs or tokens in errors or logs. Gitleaks scans the whole git history in CI. | `infra/README.md`, `.github/workflows/ci.yml` |
| Kill switch | `/nightwatch/kill_switch` stops every fix. If it can't be read, it counts as on. | spec F14, `calls.kill_switch_on` |
| Tenant isolation (section 13) | Every key starts with `TENANT#<tenant>`. Each role's table access is held to that prefix by a `dynamodb:LeadingKeys` condition (new), and no role may `Scan`. | `tenancy.tf`, `test_tenancy.py` (new) |

## Section 16: testing and quality

| Rule | Status |
|---|---|
| Unit tests for tools, runbooks, the guard, redaction and dedupe | Met: 322 tests. LLM calls use `FakeProvider`. |
| Contract tests: the diagnosis schema, and Discord and Slack signatures | Met. The investigation schema (diagnosis included) is now committed in `docs/contracts/` and tested against the code (new). |
| Integration: deploy a dev stack and run a chaos scenario end to end | Met from a laptop: `make chaos`, `make eval` (7/7, [results](https://github.com/RaagulGananathan13/nightwatch/blob/main/eval/results.md)) and the live runs in the README. Not yet a manual GitHub Actions workflow; see below. |
| Security tests: prompt-injection fixtures | Met: the two injection tests above. |
| 80% coverage on `agent`, `remediation`, `common` | Met: 89.4%, 84.8% and 94.9%. |
| Static checks | Met: ruff; mypy strict everywhere (the spec asks only for `common` and `agent`); Checkov; Bandit; pip-audit; Gitleaks. |

## Fixed in this review

1. **Slack secrets weren't redacted.** A Slack bot token or webhook in a log line would have
   reached the external LLM. Both are now redacted.
2. **Tenant isolation was a code convention, not a rule.** IAM now enforces it on all 12 roles
   that touch the table.
3. **Redaction had no allow-list.** It now keeps loopback, `0.0.0.0` and the metadata address,
   which are often the clue ("connection refused to 127.0.0.1:5432").
4. **The planner had no injection test, and the diagnosis schema no contract.** Both now do.

## Checked live

On 2026-11-03, after the tenant conditions were applied, every fenced role ran normally with no
`AccessDenied`:
- the forecast, the cost check and the MCP server;
- a bad-deploy chaos run, INC-021, from ingest through the investigation, a person's
  **Approve**, the rollback and its verification (errors 0, 0, 0 after the fix), to the
  postmortem.

## Deliberate deviations

- **The deployer user has an access key.** The spec says "no long-lived access keys anywhere".
  CI follows that rule. Local Terraform and the chaos lab run as `nightwatch-deployer`, whose
  key lives only on the owner's laptop and is limited by the `NightWatchBoundary` policy. The
  next step would be IAM Identity Center, which gives short-lived credentials for free.
- **The remediator is scoped by ARN, not by tag.** The spec suggests the condition
  `nightwatch:managed=true`. Exact ARNs are narrower: anyone allowed to tag could pull a
  resource into a tag condition, but nobody can add an ARN to a policy except through reviewed
  Terraform. Adding the tag as a second condition would mean re-checking each runbook live.
- **No GitHub Actions chaos workflow.** Running chaos from CI would need a role that can deploy
  broken code. That's more power than a manual check is worth for a one-person lab, so chaos
  runs from the laptop, as the deployer.
