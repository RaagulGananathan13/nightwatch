---
description: NightWatch is an open-source AI on-call agent for AWS. It diagnoses incidents with cited evidence, and runs a reviewed fix only after a person approves it.
hide:
  - navigation
  - toc
---

<div class="nw-hero" markdown>

<div markdown>

<span class="nw-eyebrow">Open source · Apache-2.0 · v1.0</span>

# The AI on-call agent for your AWS account

<p class="nw-lede">When an alarm fires, NightWatch gathers the evidence and posts a diagnosis to
Discord or Slack, with every claim linked to the log line or metric behind it. If a reviewed
runbook fits, it proposes the fix, and runs it only after a person clicks Approve.</p>

<div class="nw-actions" markdown>

[Get started](quickstart.md){ .md-button .md-button--primary }
[View on GitHub :fontawesome-brands-github:](https://github.com/RaagulGananathan13/nightwatch){ .md-button }

</div>

</div>

<div class="nw-demo">
  <div class="nw-demo__bar">
    <img src="assets/logo.svg" alt="">
    <b>NightWatch</b> in #incidents
    <span class="nw-demo__tag">Example</span>
  </div>
  <div class="nw-demo__body">
    <p class="nw-demo__title">INC-042 · shoplite-api · errors after a deploy</p>
    <p>Errors began 40 seconds after version 7 went live. Every new error is the same
    <code>TimeoutError</code> from the inventory call, and version 6 had none.</p>
    <ul class="nw-demo__facts">
      <li>Deploy: version 7, from the Lambda API</li>
      <li>Errors: 0 to 31 a minute, from CloudWatch</li>
      <li>Log: 212 matching lines, from CloudWatch Logs</li>
    </ul>
    <div class="nw-demo__fix">Proposed fix: roll back to version 6 (runbook <code>rollback</code>)</div>
    <div class="nw-demo__buttons">
      <span class="nw-demo__approve">Approve</span>
      <span>Reject</span>
    </div>
  </div>
</div>

</div>

<div class="nw-stats">
  <div class="nw-stat"><strong>7 of 7</strong><span>root causes found on real injected faults</span></div>
  <div class="nw-stat"><strong>46 s</strong><span>mean time from alarm to diagnosis in chat</span></div>
  <div class="nw-stat"><strong>100%</strong><span>of cited facts checked against the evidence</span></div>
  <div class="nw-stat"><strong>~1¢</strong><span>a month on AWS, measured, with an emergency stop</span></div>
</div>

## What NightWatch does

<div class="grid cards" markdown>

-   :material-bell-ring-outline:{ .lg .middle } **One incident, not fifty alarms**

    Alarms are grouped per service, deduplicated and protected against flapping. Chat gets one
    card, and it stays up to date.

-   :material-magnify-scan:{ .lg .middle } **A diagnosis with receipts**

    An agent with read-only tools gathers logs, metrics and recent changes. A guard marks any
    claim the evidence doesn't support.

-   :material-shield-check-outline:{ .lg .middle } **Fixes a person approves**

    Runbooks as code, a signed **Approve** click, then verification, with an automatic undo if
    things get worse.

-   :material-history:{ .lg .middle } **Memory and postmortems**

    Similar past incidents inform the next diagnosis, and every resolved incident gets a
    blameless postmortem.

-   :material-piggy-bank-outline:{ .lg .middle } **Free, and built to stay free**

    Always-free AWS services and free LLM tiers, a daily cost check, and an emergency stop at
    $0.50.

-   :material-robot-outline:{ .lg .middle } **Works with your tools**

    Discord and Slack, a web dashboard, `/nw` chat commands, and an MCP server for your own AI
    assistant.

</div>

## How an incident runs

<ol class="nw-steps">
  <li><strong>Alarm</strong><span>CloudWatch fires. NightWatch opens one incident for the service and posts a card.</span></li>
  <li><strong>Investigate</strong><span>Read-only tools gather evidence. The LLM names a root cause, and the guard checks each claim.</span></li>
  <li><strong>Approve</strong><span>If a runbook fits, the card offers the fix. Nothing runs until an approver clicks.</span></li>
  <li><strong>Verify and learn</strong><span>NightWatch checks that the fix worked, undoes it if not, and writes the postmortem.</span></li>
</ol>

The [architecture](architecture.md) page shows every part, and the
[evaluation](https://github.com/RaagulGananathan13/nightwatch/blob/main/eval/results.md) shows
how the numbers above were measured.

## Start here

<div class="grid cards nw-links" markdown>

-   :material-rocket-launch-outline:{ .lg .middle } [Quickstart](quickstart.md)

    Run it locally in five minutes, then deploy it to your own account.

-   :material-sitemap-outline:{ .lg .middle } [Architecture](architecture.md)

    How the parts fit, and where the trust boundaries are.

-   :material-piggy-bank-outline:{ .lg .middle } [Cost and safety](cost-and-safety.md)

    What it really costs, the emergency stop, and open-source safety.

-   :material-lock-outline:{ .lg .middle } [Security review](security-review.md)

    Every security rule, and where it is enforced.

</div>
