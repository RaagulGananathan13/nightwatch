# Security policy

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately instead: open the
repository's **Security** tab and choose **Report a vulnerability**. Only the maintainer sees
the report.

Include what you found, how to reproduce it, and what an attacker could do with it. NightWatch
is maintained by one person, so expect a first reply within a week. Once a fix ships, you'll be
credited in the advisory, unless you'd rather not be.

## Supported versions

Only the latest release on `main` gets security fixes.

## Scope

In scope: everything in this repository, including the Lambdas, the Terraform, the runbooks,
the dashboard and the chat adapters.

Out of scope: weaknesses in AWS, Discord, Slack or the LLM providers themselves, and
denial-of-service tests against someone's deployment.

## How NightWatch is secured

- [Security review](https://raagulgananathan13.github.io/nightwatch/security-review/): each
  security rule, and where it is enforced.
- [Cost and safety](https://raagulgananathan13.github.io/nightwatch/cost-and-safety/): secrets,
  contributors, and the emergency stop that switches everything off before it costs money.
