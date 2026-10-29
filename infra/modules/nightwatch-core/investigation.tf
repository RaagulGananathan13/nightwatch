# Phase 3: the incident workflow. Wait for related alarms, investigate, report.
# Phase 4 adds approval, remediation and verification (remediation.tf).
#
#   ingest ──StartExecution──► nightwatch-incident (Step Functions, Standard)
#                                ├─ LetAlertsSettle (Wait, free)
#                                ├─ Investigate ──► nightwatch-investigate (read-only + LLM)
#                                └─ Report ───────► nightwatch-report (edits the Discord message)
#
# The investigator is read-only toward the services it inspects (spec section 11). Its only
# writes go to NightWatch's own table: evidence, diagnosis, audit and LLM budget counters.

locals {
  investigate_name = "nightwatch-investigate"
  report_name      = "nightwatch-report"
  log_group_query_arns = flatten([
    for arn in var.watched.log_group_arns : [arn, "${arn}:*"]
  ])
}

# --------------------------------------------------------------------- investigate

resource "aws_cloudwatch_log_group" "investigate" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.investigate_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "investigate" {
  name                 = local.investigate_name
  path                 = "/nightwatch/"
  description          = "Read-only investigator (spec section 11): reads the watched service, writes only NightWatch state"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  tags                 = var.tags
}

resource "aws_iam_role_policy" "investigate" {
  name   = local.investigate_name
  role   = aws_iam_role.investigate.id
  policy = data.aws_iam_policy_document.investigate.json
}

data "aws_iam_policy_document" "investigate" {
  statement {
    sid = "NightWatchState"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
    ]
    resources = [aws_dynamodb_table.this.arn, "${aws_dynamodb_table.this.arn}/index/*"]
  }

  statement {
    sid       = "LlmKeys"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${local.region}:${local.account}:parameter/nightwatch/llm/*"]
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
    sid       = "ReadAlarms"
    actions   = ["cloudwatch:DescribeAlarms", "cloudwatch:DescribeAlarmHistory"]
    resources = ["arn:aws:cloudwatch:${local.region}:${local.account}:alarm:*"]
  }

  statement {
    # Checked against AWS's service authorization reference on 2026-10-29: these three have no
    # resource-level permissions, so "*" is the only possible scope.
    sid       = "NoResourceLevelPermissions"
    actions   = ["cloudwatch:GetMetricStatistics", "cloudtrail:LookupEvents", "logs:StopQuery"]
    resources = ["*"]
  }

  statement {
    sid       = "QueryWatchedLogs"
    actions   = ["logs:StartQuery", "logs:GetQueryResults"]
    resources = local.log_group_query_arns
  }

  statement {
    sid = "ReadWatchedFunctions"
    actions = [
      "lambda:GetAlias",
      "lambda:GetFunctionConcurrency",
      "lambda:GetFunctionConfiguration",
      "lambda:ListVersionsByFunction",
    ]
    resources = flatten([for arn in var.watched.function_arns : [arn, "${arn}:*"]])
  }

  statement {
    sid       = "ReadWatchedTables"
    actions   = ["dynamodb:DescribeTable"]
    resources = var.watched.table_arns
  }

  statement {
    sid       = "ReadWatchedParameters"
    actions   = ["ssm:GetParameter", "ssm:GetParameterHistory"]
    resources = var.watched.parameter_arns
  }

  statement {
    # The verify Lambda shares this read-only role.
    sid     = "WriteLogs"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "${aws_cloudwatch_log_group.investigate.arn}:*",
      "${aws_cloudwatch_log_group.verify.arn}:*",
    ]
  }
}

resource "aws_lambda_function" "investigate" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed; the evidence ledger and audit log are the trace
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:Invoked synchronously by Step Functions, which retries and catches failures
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.investigate_name
  description      = "Gathers evidence and writes an evidence-backed diagnosis (spec F3, F4)"
  role             = aws_iam_role.investigate.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.agent.handler.handler"
  memory_size      = 512
  timeout          = 150 # the agent stops itself at INVESTIGATION_SECONDS
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      TABLE_NAME            = aws_dynamodb_table.this.name
      TENANT_ID             = var.tenant_id
      LLM_PROVIDERS         = var.llm_providers
      LLM_DAILY_REQUESTS    = tostring(var.llm_daily_requests)
      INVESTIGATION_SECONDS = "90"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.investigate.name
  }

  tags = var.tags
}

# -------------------------------------------------------------------------- report

resource "aws_cloudwatch_log_group" "report" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.report_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "report" {
  name                 = local.report_name
  path                 = "/nightwatch/"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  tags                 = var.tags
}

resource "aws_iam_role_policy" "report" {
  name   = local.report_name
  role   = aws_iam_role.report.id
  policy = data.aws_iam_policy_document.report.json
}

data "aws_iam_policy_document" "report" {
  statement {
    sid       = "NightWatchState"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.this.arn]
  }

  statement {
    sid     = "ReadDiscordSecretsAndKillSwitch"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${local.region}:${local.account}:parameter${var.discord_webhook_param}",
      "arn:aws:ssm:${local.region}:${local.account}:parameter${var.discord_bot_token_param}",
      local.kill_switch_arn,
    ]
  }

  statement {
    # When the kill switch is on, the approval request answers itself ("blocked"). The
    # task-token calls have no resource-level permissions.
    sid       = "ResumeWorkflow"
    actions   = ["states:SendTaskSuccess"]
    resources = ["*"]
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
    resources = ["${aws_cloudwatch_log_group.report.arn}:*"]
  }
}

resource "aws_lambda_function" "report" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed for a single Discord edit
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:Invoked synchronously by Step Functions, which retries failures
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.report_name
  description      = "Posts diagnoses, approval requests and fix outcomes to Discord"
  role             = aws_iam_role.report.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.chat.report.handler"
  memory_size      = 256
  timeout          = 30
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      TABLE_NAME               = aws_dynamodb_table.this.name
      TENANT_ID                = var.tenant_id
      DISCORD_WEBHOOK_PARAM    = var.discord_webhook_param
      DISCORD_BOT_TOKEN_PARAM  = var.discord_bot_token_param
      DISCORD_CHANNEL_ID       = var.discord_channel_id
      APPROVAL_TIMEOUT_MINUTES = tostring(var.approval_timeout_minutes)
      KILL_SWITCH_PARAM        = var.kill_switch_param
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.report.name
  }

  tags = var.tags
}

# ------------------------------------------------------------------------ workflow

resource "aws_iam_role" "workflow" {
  name                 = "nightwatch-incident-workflow"
  path                 = "/nightwatch/"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.states_trust.json
  tags                 = var.tags
}

data "aws_iam_policy_document" "states_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_iam_role_policy" "workflow" {
  name   = "invoke-steps"
  role   = aws_iam_role.workflow.id
  policy = data.aws_iam_policy_document.workflow.json
}

data "aws_iam_policy_document" "workflow" {
  statement {
    actions = ["lambda:InvokeFunction"]
    resources = flatten([
      for arn in [
        aws_lambda_function.investigate.arn,
        aws_lambda_function.report.arn,
        aws_lambda_function.remediate.arn,
        aws_lambda_function.verify.arn,
      ] : [arn, "${arn}:*"]
    ])
  }
}

# Standard workflows: 4,000 state transitions a month are free. An incident uses about 6,
# or about 15 when a fix is approved, run and verified.
resource "aws_sfn_state_machine" "incident" {
  #checkov:skip=CKV_AWS_284:X-Ray tracing isn't needed; execution history is kept for 90 days for free
  #checkov:skip=CKV_AWS_285:Execution logging to CloudWatch is billed as vended logs; execution history is free
  name     = "nightwatch-incident"
  role_arn = aws_iam_role.workflow.arn
  type     = "STANDARD"
  definition = templatefile("${path.module}/../../../stepfunctions/incident.asl.json", {
    investigate_arn          = aws_lambda_function.investigate.arn
    report_arn               = aws_lambda_function.report.arn
    remediate_arn            = aws_lambda_function.remediate.arn
    verify_arn               = aws_lambda_function.verify.arn
    settle_seconds           = var.settle_seconds
    approval_timeout_seconds = var.approval_timeout_minutes * 60
  })
  tags = var.tags
}
