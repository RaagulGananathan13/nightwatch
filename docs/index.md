---
description: Documentation for NightWatch, an open-source on-call agent for AWS that diagnoses incidents with cited evidence and runs reviewed fixes only after a person approves them.
hide:
  - navigation
  - toc
---

<div class="nw-home" markdown>

# NightWatch documentation

NightWatch is an open-source on-call agent for services that run on AWS. When a CloudWatch alarm
fires, it opens an incident, investigates with read-only tools and a large language model, and
posts a diagnosis to Discord or Slack. Each claim in the diagnosis links to the log line, metric
or change it came from. If a reviewed runbook applies, NightWatch proposes the fix, runs it only
after an approver accepts it, verifies the result and writes a postmortem.

NightWatch is self-hosted, runs on AWS always-free services, and is released under the Apache
License 2.0.

<div class="grid cards nw-start" markdown>

-   [Quickstart](quickstart.md)

    Run the test suite locally, then deploy NightWatch to your own AWS account.

-   [Architecture](architecture.md)

    The components, how an incident moves through them, and the trust boundaries.

-   [Cost and safety](cost-and-safety.md)

    The measured running cost, the emergency stop, and how the repository is protected.

</div>

## At a glance

| Area | Detail |
|---|---|
| Input | CloudWatch alarms, logs and metrics, CloudTrail events and deploy history |
| Platform | AWS Lambda, Step Functions, DynamoDB, EventBridge and S3, within the always-free tier |
| Language models | Groq or Google Gemini free tiers, or a local model through Ollama |
| Chat | Discord and Slack, with signed approval buttons and `/nw` commands |
| Fixes | Four reviewed runbooks: roll back a Lambda alias, reset feature flags, reset Lambda concurrency, raise DynamoDB capacity |
| Interfaces | A web dashboard behind Cognito sign-in, and an MCP server for AI assistants |
| Measured cost | About $0.01 a month (October 2026) |

## Evaluation

NightWatch was evaluated against seven faults injected into the ShopLite demo application in
October 2026. The
[full results](https://github.com/RaagulGananathan13/nightwatch/blob/main/eval/results.md) list
every scenario and the scoring rules.

| Measure | Result |
|---|---|
| Root cause identified as the first hypothesis | 7 of 7 |
| Correct fix proposed | 7 of 7 |
| Hypotheses verified against the evidence | 11 of 11 |
| Mean time from alarm to diagnosis in chat | 46 seconds |
| Alerts grouped into incidents | 17 alerts into 7 incidents |

## Documentation

<div class="grid nw-map" markdown>

<div markdown>

### Get started

- [Quickstart](quickstart.md)
- [Prerequisites](prerequisites.md)
- [Deploy to AWS](deploy.md)
- [Set up Discord and Slack](chat.md)
- [Dashboard and MCP server](dashboard-and-mcp.md)

</div>

<div markdown>

### Guides

- [Watch your own service](watch-your-service.md)
- [Connect an AWS account](connect.md)
- [Day-to-day operation](operations.md)
- [Chaos testing](chaos.md)
- [Upgrade and tear down](upgrade-and-teardown.md)
- [Troubleshooting](troubleshooting.md)

</div>

<div markdown>

### Concepts

- [Architecture](architecture.md)
- [Cost and safety](cost-and-safety.md)
- [Security review](security-review.md)

</div>

<div markdown>

### Reference

- [Configuration](reference/configuration.md)
- [Commands](reference/commands.md)
- [Runbooks](reference/runbooks.md)
- [Glossary](reference/glossary.md)
- [Design decisions](decisions/README.md)

</div>

<div markdown>

### Project

- [Changelog](changelog.md)
- [Contributing](https://github.com/RaagulGananathan13/nightwatch/blob/main/CONTRIBUTING.md)
- [Security policy](https://github.com/RaagulGananathan13/nightwatch/blob/main/SECURITY.md)

</div>

</div>

</div>
