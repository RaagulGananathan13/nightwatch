# 0012. Approval-gated remediation: runbooks, Discord buttons and verification

- Status: accepted
- Date: 2026-10-29
- Spec sections: F5, F7, F14, 9, 11 (Phase 4)

## Context

A diagnosis says what broke. The fix should be one click away, but an AI must never change
production on its own judgement (spec principle 1). Four risks drive this design:

- **Prompt injection.** Log lines are attacker-controlled text. If the model could choose a
  fix or its parameters, a crafted log line could aim a change at anything.
- **Forged clicks.** Discord needs a public URL to deliver button clicks, so anyone on the
  internet can send requests to it.
- **Stale approvals.** By the time someone clicks, the resource may have changed.
- **Cost.** It must all stay free. API Gateway is free for only 12 months, and a Lambda can't
  sit idle for 30 minutes waiting for a human.

## Decision

1. **Runbooks are code.** Each fix is a YAML file in `runbooks/`, validated by a pydantic model.
   `runbooks/runbook.schema.json` is generated from that model, and a test fails if the two
   drift apart. A runbook may make only one of four allow-listed AWS calls:
   - `lambda.update_alias`
   - `lambda.delete_function_concurrency`
   - `ssm.put_parameter`
   - `dynamodb.update_table`

   `auto_approve: true` is accepted only on `low` risk runbooks, and none of the shipped
   runbooks use it.
2. **Evidence picks the fix and the LLM doesn't.** The planner finds *signals* in the structured
   results of the read-only tools:
   - a deploy that went live up to 30 minutes before the incident;
   - a feature-flag change;
   - a capacity drop;
   - a function throttled to zero.

   The diagnosis only decides *whether* to propose a fix: a hypothesis with at least 50%
   confidence must cite that evidence, or the model must name the runbook. A runbook the model
   names is only a tiebreak hint. Parameters come only from four places:
   - `signal.*` (AWS API data)
   - `state.*` (the resource's current state)
   - `service.*` (the service catalogue)
   - `literal:` (the runbook itself)

   Every rendered call must target a resource listed for the service in `services.yaml`.
3. **Roll back to what was live, not to N-1.** `deploy_lambda` now records the version that was
   live in each new version's description (`prev=5`). After the Phase 3 chaos runs, version 6
   was itself a bad build, so "the previous version number" would have rolled back onto a bug.
   With no `prev=`, NightWatch proposes no rollback rather than guess.
4. **The wait is a Step Functions task token.**
   - **Request.** The report Lambda stores the token on the action item, then posts the card
     through the Discord bot, because webhook messages can't carry buttons that reach us. The
     card pings the approvers and threads under the incident's message.
   - **Wait.** The workflow then sleeps for free.
   - **Escalate.** After 30 minutes it times out into `Escalate`: the card is marked expired,
     the approvers are pinged, and nothing changes.
5. **Clicks are verified twice.** The interactions Lambda sits behind a function URL with
   `NONE` auth, which Discord requires. Since October 2025 a public URL needs both
   `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`, and the second is limited to calls
   through the URL. In code, each request is checked in this order:
   - the Ed25519 signature over timestamp and body, using PyNaCl and the application's public
     key, else **401**;
   - the timestamp is within 5 minutes, so captured requests can't be replayed;
   - the button names a real action that is still `awaiting_approval`;
   - the person clicking is in the service's `approvers` list in `services.yaml`.

   Every decision, and every refusal, goes to the audit log with who and when.
6. **Every status change is a conditional write.** Each change to an action item is guarded by
   the item's version. So a double click, two approvers, or a click racing the timeout can only
   produce one decision.
7. **The remediator checks again before acting.** It runs as its own role, which can make only
   the allow-listed calls on the watched resources. Before it changes anything it checks:
   - the kill switch (`/nightwatch/kill_switch`; anything but `false` counts as on, and an
     unreadable switch counts as on);
   - that the stored call is exactly what the runbook renders from the stored params, so an
     edited database item can't aim it elsewhere;
   - that the resource is still in the state the approver saw.

   A retried invocation never runs a fix twice.
8. **Verification.** The verify Lambda reads the runbook's metric for every full minute after
   the fix, using the read-only role and `GetMetricStatistics`. It compares them with the 5
   minutes before the fix and gives one verdict:
   - `verified`: at or below the limit;
   - `not_fixed`: still above the limit, but not worse;
   - `worse`: higher than before the fix. The runbook's `rollback` runs automatically (spec F7)
     and the card says so;
   - `no_data`: no traffic, so nothing to judge.

## Consequences

- One click fixes a known failure. The audit log records the whole chain:
  `remediation_proposed`, `approval_requested`, `approval_decided`, `remediation_executed`,
  `remediation_verified`.
- A forged or replayed request costs one Lambda invocation that returns 401. The URL isn't
  published, and the $1 budget alert covers abuse. New accounts can't reserve concurrency to cap
  it further (ADR 0007).
- Each incident that gets a fix uses about 15 state transitions, against 4,000 free a month.
- Fixes can be only what a reviewed runbook allows. A new kind of fix needs code review: a call
  in `calls.py` and a statement in the remediator's IAM policy, not just a YAML file.
- The remediator role is scoped by ARN, not by the spec's `nightwatch:managed` tag. ARNs are
  stricter, because anyone allowed to tag a resource could otherwise bring it into scope.
