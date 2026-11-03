# Phase 8, F17: the cost guardian. A cost spike becomes an incident, investigated like any other.
#
#   EventBridge Scheduler (daily) ──┐
#                                   ├─► nightwatch-cost ─► the alarm pipeline: incident, Discord,
#   AWS Budget alert ─► SNS topic ──┘                      investigation, postmortem
#
# The daily check reads free CloudWatch usage data; a budget alert is AWS's own spend figure.
# Cost Explorer is never called: it charges per request.

locals {
  cost_name = "nightwatch-cost"
}

resource "aws_cloudwatch_log_group" "cost" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.cost_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "cost" {
  name                 = local.cost_name
  path                 = "/nightwatch/"
  description          = "Cost guardian: reads usage, raises cost incidents through the alarm pipeline"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  tags                 = var.tags
}

resource "aws_iam_role_policy" "cost" {
  name   = local.cost_name
  role   = aws_iam_role.cost.id
  policy = data.aws_iam_policy_document.cost.json
}

data "aws_iam_policy_document" "cost" {
  statement {
    # The same incident writes as the ingest Lambda: cost alerts go through its pipeline.
    sid = "Incidents"
    actions = [
      "dynamodb:ConditionCheckItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
    ]
    resources = [aws_dynamodb_table.this.arn, "${aws_dynamodb_table.this.arn}/index/*"]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = local.tenant_keys
    }
  }

  statement {
    # Checked against AWS's service authorization reference: these have no resource-level
    # permissions. Free-tier usage is account-wide, so the check reads every function.
    sid       = "NoResourceLevelPermissions"
    actions   = ["cloudwatch:GetMetricStatistics", "lambda:ListFunctions"]
    resources = ["*"]
  }

  statement {
    sid       = "ReadWatchedFunctions"
    actions   = ["lambda:GetFunctionConfiguration"]
    resources = var.watched.function_arns
  }

  statement {
    sid       = "ReadProvisionedCapacity"
    actions   = ["dynamodb:DescribeTable"]
    resources = concat(var.watched.table_arns, [aws_dynamodb_table.this.arn])
  }

  statement {
    sid     = "ReadDiscordWebhook"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${local.region}:${local.account}:parameter${var.discord_webhook_param}",
      local.slack_token_arn,
    ]
  }

  statement {
    sid       = "DecryptViaSsmOnly"
    actions   = ["kms:Decrypt"]
    resources = ["arn:aws:kms:${local.region}:${local.account}:key/*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${local.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "StartInvestigation"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.incident.arn]
  }

  statement {
    sid       = "StartPostmortem"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.postmortem.arn]
  }

  statement {
    # The emergency stop (stop.py): a reserved concurrency of 0 switches a function off. Only
    # NightWatch's and ShopLite's own functions, never another project's.
    sid     = "EmergencyStop"
    actions = ["lambda:PutFunctionConcurrency"]
    resources = [
      "arn:aws:lambda:${local.region}:${local.account}:function:nightwatch-*",
      "arn:aws:lambda:${local.region}:${local.account}:function:shoplite-*",
    ]
  }

  statement {
    sid       = "KillSwitchOn"
    actions   = ["ssm:PutParameter"]
    resources = [local.kill_switch_arn]
  }

  statement {
    sid       = "ShrinkCapacity"
    actions   = ["dynamodb:UpdateTable"]
    resources = concat(var.watched.table_arns, [aws_dynamodb_table.this.arn])
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.cost.arn}:*"]
  }
}

resource "aws_lambda_function" "cost" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed; the audit log records every cost alert
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:A missed daily check runs again the next morning; budget alerts are also emailed
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.cost_name
  description      = "Cost guardian: daily usage check and budget alerts, as incidents (spec F17)"
  role             = aws_iam_role.cost.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.cost.handler.handler"
  memory_size      = 256
  timeout          = 120
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      TABLE_NAME            = aws_dynamodb_table.this.name
      TENANT_ID             = var.tenant_id
      DISCORD_WEBHOOK_PARAM = var.discord_webhook_param
      FLAP_WINDOW_MINUTES   = tostring(var.flap_window_minutes)
      STATE_MACHINE_ARN     = aws_sfn_state_machine.incident.arn
      POSTMORTEM_FUNCTION   = aws_lambda_function.postmortem.function_name
      SLACK_BOT_TOKEN_PARAM = local.slack_token_param
      SLACK_CHANNEL_ID      = var.slack_channel_id
      KILL_SWITCH_PARAM     = var.kill_switch_param
      STOP_AT_USD           = tostring(var.stop_at_usd)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.cost.name
  }

  tags = var.tags
}

resource "aws_scheduler_schedule" "cost" {
  #checkov:skip=CKV_AWS_297:Customer-managed KMS keys cost $1/month; the schedule holds no secrets (ADR 0007)
  name                         = local.cost_name
  description                  = "The daily cost check, after the forecast"
  schedule_expression          = "cron(30 7 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.cost.arn
    role_arn = aws_iam_role.coach_schedule.arn # one role runs NightWatch's scheduled jobs

    retry_policy {
      maximum_retry_attempts = 1
    }
  }
}

# ----------------------------------------------------------------------- budget alerts

resource "aws_sns_topic" "budget" {
  #checkov:skip=CKV_AWS_26:AWS Budgets can't publish to a topic under the AWS-managed key, and a customer-managed key costs $1/month (ADR 0007)
  name = "nightwatch-budget-alerts"
  tags = var.tags
}

resource "aws_sns_topic_policy" "budget" {
  arn    = aws_sns_topic.budget.arn
  policy = data.aws_iam_policy_document.budget_topic.json
}

data "aws_iam_policy_document" "budget_topic" {
  statement {
    sid       = "BudgetsPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.budget.arn]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_sns_topic_subscription" "budget" {
  topic_arn = aws_sns_topic.budget.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.cost.arn
}

resource "aws_lambda_permission" "budget" {
  statement_id  = "BudgetAlerts"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.budget.arn
}

# ----------------------------------------------------------------------- emergency stop
# Lambda calls running away (a flood on the public chat endpoint, or a loop) are caught here in
# minutes, long before a bill shows them. The alarm calls the cost Lambda, which stops everything.

resource "aws_cloudwatch_metric_alarm" "runaway" {
  alarm_name          = "nightwatch-runaway-invocations"
  alarm_description   = "Lambda calls ran away: the emergency stop switches NightWatch and ShopLite off."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.runaway_invocations
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_lambda_function.cost.arn]
  tags                = var.tags
}

resource "aws_lambda_permission" "runaway" {
  statement_id  = "RunawayAlarm"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost.function_name
  principal     = "lambda.alarms.cloudwatch.amazonaws.com"
  source_arn    = aws_cloudwatch_metric_alarm.runaway.arn
}
