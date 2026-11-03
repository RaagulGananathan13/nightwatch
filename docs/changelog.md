# Changelog

Notable changes to NightWatch. Each version is a git tag and matches one build phase of the
[specification](https://github.com/RaagulGananathan13/nightwatch/blob/main/NightWatch-SPEC.md).

## Unreleased

- **Emergency stop.** NightWatch switches itself and ShopLite off when real spend reaches $0.50
  in a month, when Lambda calls exceed 30,000 in 5 minutes, or when a free tier is used up.
  `make resume` switches it back on ([ADR 0024](decisions/0024-emergency-stop.md)).
- **Measured cost.** The documentation now states the measured cost, about $0.01 a month,
  instead of $0.
- **Project policies.** A security policy with private vulnerability reporting, and a
  contributing guide.
- **Documentation.** A reorganised site, with this changelog, a [commands](reference/commands.md)
  reference and a [glossary](reference/glossary.md).

## 1.0 (2026-11-03)

Production readiness and launch.

- A security review against the specification's security and quality rules.
- Tenant isolation enforced by IAM on every role that reads or writes the table.
- A CloudFormation template to connect another AWS account through a read-only role.
- This documentation site.

## 0.9 (2026-11-02)

- Pull requests that raise settings found to be too small.
- An alert-quality coach, daily forecasts against known limits, and a public status page.
- An MCP server, the cost guardian, and Slack alongside Discord.

## 0.8 (2026-10-30)

- A web dashboard behind Cognito sign-in, served through CloudFront.

## 0.7 (2026-10-30)

- Incident memory, automatic undo of fixes that make things worse, postmortems, and the `/nw`
  chat commands.

## 0.6 (2026-10-30)

- A chaos lab of seven fault scenarios, and an evaluation harness that scores accuracy and time
  to diagnosis.

## 0.5 (2026-10-29)

- Runbooks, approval buttons in Discord, remediation with verification, an audit log and a kill
  switch.

## 0.4 (2026-10-29)

- The investigation agent, the evidence ledger and the guard.

## 0.3 (2026-10-28)

- Alert ingest, one incident per service, Discord notifications and the first chaos script.

## 0.2 (2026-10-28)

- The Terraform base, a $1 budget alert, GitHub OIDC, and the ShopLite demo application with its
  alarms.

## 0.1 (2026-10-28)

- The repository, tooling and CI.
