# Connect an AWS account

NightWatch can investigate services in another AWS account. That account gets one read-only
role, made by [nightwatch-connect.yaml](nightwatch-connect.yaml). NightWatch assumes the role
with STS, and only with an **ExternalId** made for that account, so someone who learns the
role's name can't use it ([ADR 0023](../docs/decisions/0023-connected-accounts.md)).

The role reads metrics, alarms, logs, CloudTrail events and the settings of Lambda functions
and DynamoDB tables. It reads an SSM parameter only when the parameter is tagged
`nightwatch:readable=true`. It never changes anything, so NightWatch proposes no fixes in a
connected account, and it can't read table items, objects in buckets or secrets.

## Steps

1. **NightWatch's operator makes the ExternalId** (from a NightWatch checkout):

   ```bash
   python -m scripts.connect new-id --tenant <name>
   ```

   This prints the ExternalId once and keeps it in SSM.
2. **The account's owner creates the stack.** Open **CloudFormation → Create stack → With new
   resources** in the account to connect, then upload `nightwatch-connect.yaml`. Give it two
   values:
   - `NightWatchAccountId`: the account NightWatch runs in;
   - `ExternalId`: the value from step 1.

   When the stack finishes, copy its `RoleArn` output.
3. **The operator adds the service** to `nightwatch/services.yaml` with an `account` block:

   ```yaml
   account:
     role_arn: arn:aws:iam::<their account>:role/NightWatchReadOnly-us-east-2
     external_id_param: /nightwatch/tenants/<name>/external_id
     region: us-east-2
     llm_key_param: /nightwatch/llm/tenants/<name>/groq   # optional: their own Groq key
   ```

4. **The operator checks it:**

   ```bash
   python -m scripts.connect check --tenant <name> --role <RoleArn>
   ```

   This assumes the role, reads through it, and shows that the role refuses anyone without the
   ExternalId.

## Costs in the connected account

Assuming the role is free. Metrics and CloudTrail lookups stay inside the always-free API
allowance. Logs Insights queries are charged per GB scanned, which is pennies at this scale.

## Not yet

Alarms in the connected account don't reach NightWatch by themselves yet. That needs an
EventBridge rule there that forwards alarm changes to NightWatch's event bus. Until then,
`/nw ask` and the MCP server investigate a connected service on request.
