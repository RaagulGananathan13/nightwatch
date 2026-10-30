# Phase 8, F12: predictive warnings. Every morning a small Lambda fits a straight-line trend to
# the last 14 days and compares it with each limit: the free tiers, each service's error budget,
# each watched table's read capacity. Free CloudWatch reads only; no paid anomaly detection.

locals {
  forecast_name = "nightwatch-forecast"
}

resource "aws_cloudwatch_log_group" "forecast" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.forecast_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "forecast" {
  name                 = local.forecast_name
  path                 = "/nightwatch/"
  description          = "Forecasts: reads metrics and table capacity, writes its report"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  tags                 = var.tags
}

resource "aws_iam_role_policy" "forecast" {
  name   = local.forecast_name
  role   = aws_iam_role.forecast.id
  policy = data.aws_iam_policy_document.forecast.json
}

data "aws_iam_policy_document" "forecast" {
  statement {
    sid       = "NightWatchState"
    actions   = ["dynamodb:PutItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.this.arn]
  }

  statement {
    # Checked against AWS's service authorization reference: these have no resource-level
    # permissions. Free-tier usage is account-wide, so the forecast reads every function.
    sid       = "NoResourceLevelPermissions"
    actions   = ["cloudwatch:GetMetricStatistics", "lambda:GetAccountSettings", "lambda:ListFunctions"]
    resources = ["*"]
  }

  statement {
    sid       = "ReadWatchedTables"
    actions   = ["dynamodb:DescribeTable"]
    resources = var.watched.table_arns
  }

  statement {
    sid       = "ReadDiscordWebhook"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${local.region}:${local.account}:parameter${var.discord_webhook_param}"]
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
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.forecast.arn}:*"]
  }
}

resource "aws_lambda_function" "forecast" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed for a daily report
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:A missed forecast is simply made the next morning
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.forecast_name
  description      = "Daily forecasts: free tiers, error budgets, capacity (spec F12)"
  role             = aws_iam_role.forecast.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.predict.handler.handler"
  memory_size      = 256
  timeout          = 60
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      TABLE_NAME            = aws_dynamodb_table.this.name
      TENANT_ID             = var.tenant_id
      DISCORD_WEBHOOK_PARAM = var.discord_webhook_param
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.forecast.name
  }

  tags = var.tags
}

resource "aws_scheduler_schedule" "forecast" {
  #checkov:skip=CKV_AWS_297:Customer-managed KMS keys cost $1/month; the schedule holds no secrets (ADR 0007)
  name                         = local.forecast_name
  description                  = "The daily forecast"
  schedule_expression          = "cron(0 7 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.forecast.arn
    role_arn = aws_iam_role.coach_schedule.arn # one role runs NightWatch's scheduled reports

    retry_policy {
      maximum_retry_attempts = 1
    }
  }
}
