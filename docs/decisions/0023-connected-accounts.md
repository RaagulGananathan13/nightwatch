# 0023. Connected accounts: a read-only role, an ExternalId and STS

- Status: accepted
- Date: 2026-11-03
- Spec sections: 13 (path to real users), Phase 9

## Context

NightWatch so far watched services in its own AWS account. The spec's path to real users
starts with "connect your AWS account": a one-click CloudFormation template that makes a
cross-account read-only role with an ExternalId, which NightWatch assumes with STS. A
connected account must never trust NightWatch with more than reading, and a stranger who
learns the role's ARN must not be able to use it.

## Decision

1. **One role per connected account, made by a template.** `connect/nightwatch-connect.yaml`
   makes the role `NightWatchReadOnly-<region>`. It reads:
   - metrics, alarms, logs (Logs Insights) and CloudTrail events;
   - the settings of Lambda functions and DynamoDB tables;
   - SSM parameters, only when tagged `nightwatch:readable=true`.

   It holds no write action, and it can't read table items, objects in buckets or secrets.
   Tests check these promises against the template file itself.
2. **The ExternalId stops a confused deputy.** The role's trust policy accepts NightWatch's
   account only when the request carries the ExternalId that NightWatch made for that tenant:
   random, stored in SSM, never reused. On NightWatch's side, the shared `read_only_tools`
   policy (used by the investigator and the MCP server) may assume only roles named
   `NightWatchReadOnly-*`, and only with an ExternalId.
3. **The catalogue says where a service runs.** A service in `services.yaml` may carry an
   `account` block: role ARN, ExternalId parameter, region and an optional LLM key.
   - Its investigations, `/nw ask` answers and MCP tool calls read through the assumed role.
     The tools are unchanged; only their clients come from the other account.
   - Credentials are reused until five minutes before they end.
4. **Connected accounts are read-only.** NightWatch proposes no fixes in them, and the audit log
   says why. The forecast and the cost guardian keep to NightWatch's own account, whose bill
   they guard.
5. **Bring your own LLM key.** A tenant may name its own Groq key under `/nightwatch/llm/`, so
   its investigations don't spend the operator's free allowance.

## Consequences

- Free: STS calls cost nothing. The connected account pays for its own Logs Insights scans,
  which are pennies at this scale.
- The deployer needs `sts:AssumeRole` on `NightWatchReadOnly-*` roles: a boundary change,
  applied by re-running `create-deployer.sh` in CloudShell.
- A connected account's alarms don't reach NightWatch by themselves yet. That needs an
  EventBridge rule there forwarding alarm changes to NightWatch's event bus. Until then, a
  connected service is investigated on request (`/nw ask`, the MCP server).
- The check was run in NightWatch's own account, which played the customer: the same template,
  trust policy and ExternalId as a real second account.
