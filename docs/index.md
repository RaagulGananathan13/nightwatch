# NightWatch

**An open-source AI on-call agent for AWS, at $0 a month.** When an alarm fires, NightWatch
groups it into one incident and gathers evidence with read-only tools. It then posts a diagnosis
in Discord or Slack, where every claim cites the log line or metric it came from. If a reviewed
runbook fits, it proposes the fix, runs it only after a person clicks **Approve**, checks that
it worked, and writes the postmortem.

## Measured, not claimed

- **Root cause found:** 7 of 7 real injected faults
  ([evaluation](https://github.com/RaagulGananathan13/nightwatch/blob/main/eval/results.md)).
- **Speed:** the diagnosis reaches chat a mean **46 seconds** after the alarm.
- **Evidence:** every cited fact checks out against what NightWatch gathered, enforced by a guard
  that marks unsupported claims "unverified".
- **Fixes:** the right fix was proposed every time, and verified after the human approved it.
- **Cost:** $0 a month, on AWS always-free services and free LLM tiers.

## What it does

| Area | What you get |
|---|---|
| Incidents | Alarms grouped per service, one chat card each, kept up to date |
| Diagnosis | An evidence-linked root cause from an agent with read-only tools and a hallucination guard |
| Fixes | Runbooks as code, a human approval in Discord or Slack, verification and automatic undo |
| Memory | Similar past incidents and an automatic postmortem for each resolved one |
| Beyond the incident | Pull requests for settings that are too small, an alert-noise coach, daily forecasts, a public status page, a cost guardian |
| Your tools | A web dashboard, `/nw` chat commands, and an MCP server for your own AI assistant |
| Your accounts | Connect another AWS account through a read-only role ([how](connect.md)) |

## Where to go next

- [Quickstart](quickstart.md): try it locally in five minutes, then deploy it.
- [Architecture](architecture.md): how the pieces fit.
- [Security review](security-review.md): every security rule, and where it is enforced.
- [Design decisions](decisions/README.md): why it is built this way.
