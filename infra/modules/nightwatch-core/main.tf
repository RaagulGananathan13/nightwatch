# NightWatch's control plane, Phase 2: alarm ingest, incidents and Discord notifications.
#
#   CloudWatch alarm changes ──► EventBridge rule ──► nightwatch-ingest Lambda ──► DynamoDB
#                                                             └──► Discord (one message per incident)
#
# NightWatch's own code is deployed by Terraform (make plan / make apply), the human-approved path.
# ShopLite's code is deployed by CI (ADR 0008).

locals {
  ingest_name = "nightwatch-ingest"
  account     = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.region
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ---------------------------------------------------------------------------- table

resource "aws_dynamodb_table" "this" {
  #checkov:skip=CKV_AWS_28:Point-in-time recovery is billed per GB-month (ADR 0007)
  #checkov:skip=CKV_AWS_119:Customer-managed KMS keys cost $1/month; the AWS-owned key is free (ADR 0007)
  #checkov:skip=CKV2_AWS_16:Auto scaling could push capacity past the 25-unit free tier (ADR 0002)
  name           = "nightwatch"
  billing_mode   = "PROVISIONED" # on-demand is not in the always-free tier (ADR 0002)
  read_capacity  = var.read_capacity
  write_capacity = var.write_capacity
  hash_key       = "PK"
  range_key      = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "GSI1PK"
    type = "S"
  }

  attribute {
    name = "GSI1SK"
    type = "S"
  }

  # Incidents by status, newest first: TENANT#<t>#STATUS#<status> / <opened_at>#<id> (ADR 0009).
  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
    read_capacity   = var.index_capacity
    write_capacity  = var.index_capacity
  }

  # Raw alerts expire after 90 days; incidents and audit entries don't have this attribute.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = var.tags
}

# ----------------------------------------------------------------- shared code layer

resource "aws_lambda_layer_version" "deps" {
  layer_name               = "nightwatch-deps"
  description              = "Third-party Python packages for NightWatch (requirements-lambda.txt)"
  filename                 = var.layer_path
  source_code_hash         = filebase64sha256(var.layer_path)
  compatible_runtimes      = ["python3.13"]
  compatible_architectures = ["arm64"]
}

# ------------------------------------------------------------------------- ingest

resource "aws_cloudwatch_log_group" "ingest" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.ingest_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# Alerts that still fail after Lambda's retries land here instead of being lost.
resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "nightwatch-ingest-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum
  sqs_managed_sse_enabled   = true    # free server-side encryption
  tags                      = var.tags
}

resource "aws_iam_role" "ingest" {
  name                 = local.ingest_name
  path                 = "/nightwatch/"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  tags                 = var.tags
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = local.ingest_name
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

data "aws_iam_policy_document" "ingest" {
  statement {
    sid = "Incidents"
    actions = [
      "dynamodb:ConditionCheckItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
    ]
    resources = [aws_dynamodb_table.this.arn, "${aws_dynamodb_table.this.arn}/index/*"]
  }

  statement {
    sid       = "ReadAlarmTags" # decides which alarms are NightWatch's business
    actions   = ["cloudwatch:ListTagsForResource"]
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
    sid       = "DeadLetters"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest_dlq.arn]
  }

  statement {
    sid       = "StartInvestigation"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.incident.arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }
}

resource "aws_lambda_function" "ingest" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed; logs, metrics and the audit log are the evidence
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.ingest_name
  description      = "Turns CloudWatch alarm changes into NightWatch incidents"
  role             = aws_iam_role.ingest.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.ingest.handler.handler"
  memory_size      = 256
  timeout          = 30
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
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.ingest_dlq.arn
  }

  logging_config {
    log_format = "Text" # the code writes one JSON line per event itself
    log_group  = aws_cloudwatch_log_group.ingest.name
  }

  tags = var.tags
}

resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name                = aws_lambda_function.ingest.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

# ---------------------------------------------------------------------- eventbridge

# Every alarm in the region going to ALARM or back to OK. AWS service events on the default
# bus are free; the Lambda ignores alarms that aren't tagged nightwatch:service.
resource "aws_cloudwatch_event_rule" "alarm_changes" {
  name        = "nightwatch-alarm-state-change"
  description = "CloudWatch alarm state changes for NightWatch"

  event_pattern = jsonencode({
    source        = ["aws.cloudwatch"]
    "detail-type" = ["CloudWatch Alarm State Change"]
    detail = {
      state = { value = ["ALARM", "OK"] }
    }
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "ingest" {
  rule = aws_cloudwatch_event_rule.alarm_changes.name
  arn  = aws_lambda_function.ingest.arn

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 10
  }
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.alarm_changes.arn
}
