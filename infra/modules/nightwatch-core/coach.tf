# Phase 8, F11: the alert-quality coach. Every Monday it scores each alarm on last week's
# incidents, suggests tuning, stores the report for the dashboard and posts a Discord digest.
# One run a week of a small Lambda: far inside the free tier.

locals {
  coach_name = "nightwatch-coach"
}

resource "aws_cloudwatch_log_group" "coach" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.coach_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "coach" {
  name                 = local.coach_name
  path                 = "/nightwatch/"
  description          = "Alert-quality coach: reads incidents and alarm settings, writes its report"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  tags                 = var.tags
}

resource "aws_iam_role_policy" "coach" {
  name   = local.coach_name
  role   = aws_iam_role.coach.id
  policy = data.aws_iam_policy_document.coach.json
}

data "aws_iam_policy_document" "coach" {
  statement {
    sid       = "NightWatchState"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.this.arn, "${aws_dynamodb_table.this.arn}/index/*"]
  }

  statement {
    sid       = "ReadAlarmSettings"
    actions   = ["cloudwatch:DescribeAlarms"]
    resources = ["arn:aws:cloudwatch:${local.region}:${local.account}:alarm:*"]
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
    resources = ["${aws_cloudwatch_log_group.coach.arn}:*"]
  }
}

resource "aws_lambda_function" "coach" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed for a weekly report
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:A missed weekly report is simply written the next week
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.coach_name
  description      = "Weekly alert-quality report: noisy alarms and how to tune them (spec F11)"
  role             = aws_iam_role.coach.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.coach.handler.handler"
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
    log_group  = aws_cloudwatch_log_group.coach.name
  }

  tags = var.tags
}

resource "aws_scheduler_schedule" "coach" {
  #checkov:skip=CKV_AWS_297:Customer-managed KMS keys cost $1/month; the schedule holds no secrets (ADR 0007)
  name                         = local.coach_name
  description                  = "The weekly alert-quality report"
  schedule_expression          = "cron(0 8 ? * MON *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.coach.arn
    role_arn = aws_iam_role.coach_schedule.arn

    retry_policy {
      maximum_retry_attempts = 1
    }
  }
}

resource "aws_iam_role" "coach_schedule" {
  name                 = "nightwatch-coach-schedule"
  path                 = "/nightwatch/"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.scheduler_trust.json
  tags                 = var.tags
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_iam_role_policy" "coach_schedule" {
  name   = "invoke-coach"
  role   = aws_iam_role.coach_schedule.id
  policy = data.aws_iam_policy_document.coach_schedule.json
}

data "aws_iam_policy_document" "coach_schedule" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.coach.arn]
  }
}
