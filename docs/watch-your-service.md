# Watch your own service

The reference deployment watches ShopLite, the demo application. This guide adds a service of
your own that runs in the same AWS account. For a service in another account, see
[Connect an AWS account](connect.md) instead.

NightWatch can only investigate resources you list explicitly, and every fix is limited to
those resources. Adding a service takes four changes:

1. allow the resources in the permissions boundary;
2. describe the service in the catalogue;
3. let NightWatch's roles read it;
4. give it alarms.

The example service is called `orders`, with a Lambda function `orders-api` and a DynamoDB table
`orders`. Replace these names with yours.

## Before you start

Check what the runbooks need if you want NightWatch to propose fixes:

| Runbook | Needs |
|---|---|
| `rollback-lambda-alias` | The function is called through an alias (for example `live`) that points at a published version |
| `reset-feature-flags` | Feature flags kept as JSON in one SSM parameter |
| `reset-lambda-concurrency` | Nothing extra |
| `raise-dynamodb-capacity` | A table in provisioned capacity mode |

A service with none of these still gets incidents and diagnoses. NightWatch just proposes no
fix. See the [runbooks reference](reference/runbooks.md).

## 1. Allow the resources in the permissions boundary

The `NightWatchBoundary` policy limits every NightWatch role, and the deployer, to resources
named `nightwatch-*` and `shoplite-*`. It also allows read-only listing across the region.

1. In `infra/bootstrap/nightwatch-boundary-policy.json`, add your resources to the `Resource`
   list of the `OwnRegionalResources` statement. Keep the `__REGION__` and `__ACCOUNT_ID__`
   placeholders, and name resources exactly rather than with wide wildcards:

    ```json
    "arn:aws:lambda:__REGION__:__ACCOUNT_ID__:function:orders-api",
    "arn:aws:logs:__REGION__:__ACCOUNT_ID__:log-group:/aws/lambda/orders-api",
    "arn:aws:logs:__REGION__:__ACCOUNT_ID__:log-group:/aws/lambda/orders-api:*",
    "arn:aws:dynamodb:__REGION__:__ACCOUNT_ID__:table/orders",
    "arn:aws:cloudwatch:__REGION__:__ACCOUNT_ID__:alarm:orders-*"
    ```

2. Commit the change, then in CloudShell, pull it and re-run the setup script. The script
   updates the policy in place and leaves the access key alone:

    ```bash
    cd nightwatch && git pull
    bash infra/bootstrap/create-deployer.sh
    ```

!!! warning "The boundary is the outer limit"
    Whatever you add here, the deployer and every NightWatch role could act on. Each role's own
    policy is still narrower: the investigator only reads, and the remediator makes four
    specific calls. Add only what NightWatch should watch.

## 2. Describe the service in the catalogue

Add an entry to `nightwatch/services.yaml`. The investigator reads only the resources listed
here, so a log line or a model's answer can't point it anywhere else.

```yaml
services:
  orders:
    description: Order API. One Lambda behind an alias, backed by DynamoDB.
    owner: <your GitHub account>
    repository: <your-account>/<orders-repo>
    lambda_functions: [orders-api]
    lambda_alias: live
    log_groups: [/aws/lambda/orders-api]
    dynamodb_tables: [orders]
    runbooks: [rollback-lambda-alias, reset-lambda-concurrency, raise-dynamodb-capacity]
    approvers: ["<your Discord user id>"]
    slo: 99.5
    public_name: Orders
```

Every field is described in the [configuration reference](reference/configuration.md#service-catalogue).

## 3. Let NightWatch's roles read it

In `infra/envs/dev/main.tf`, the `watched` block of the `nightwatch_core` module lists what the
investigator may read and what fixes may change. Look up your resources and add their ARNs:

```hcl
data "aws_lambda_function" "orders" {
  function_name = "orders-api"
}

data "aws_dynamodb_table" "orders" {
  name = "orders"
}

module "nightwatch_core" {
  # ...
  watched = {
    function_arns  = [module.shoplite.function_arn, data.aws_lambda_function.orders.arn]
    log_group_arns = [module.shoplite.log_group_arn, "arn:aws:logs:us-east-2:${local.account_id}:log-group:/aws/lambda/orders-api"]
    table_arns     = [module.shoplite.table_arn, data.aws_dynamodb_table.orders.arn]
    parameter_arns = [module.shoplite.flags_parameter_arn]
  }
}
```

## 4. Give it alarms

NightWatch reacts to CloudWatch alarms tagged with the service's name. Alarms without the tag
are ignored. Use these tags:

| Tag | Value |
|---|---|
| `nightwatch:service` | The service's name in the catalogue, for example `orders` |
| `nightwatch:severity` | `high`, `medium` or `low` |

For example, in Terraform:

```hcl
resource "aws_cloudwatch_metric_alarm" "orders_errors" {
  alarm_name          = "orders-api-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = "orders-api" }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  tags = {
    "nightwatch:service"  = "orders"
    "nightwatch:severity" = "high"
  }
}
```

!!! warning "Stay inside the free tier"
    The free tier allows 10 CloudWatch alarm metrics per account. The reference deployment uses
    8. Add your alarms' metric count to `alarm_metrics` in the `locals` block of
    `infra/envs/dev/main.tf`, so that the free-tier guard counts them. To free up room, remove
    the ShopLite demo's `alarms` module.

## 5. Deploy and test

```bash
make plan
make apply
```

To check the whole path without breaking anything, set the alarm to `ALARM` by hand. NightWatch
opens an incident within a minute or two, and the alarm returns to its real state at its next
evaluation:

```bash
aws cloudwatch set-alarm-state --profile nightwatch --alarm-name orders-api-errors --state-value ALARM --state-reason "Testing NightWatch"
```
