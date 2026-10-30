# Phase 4: approval-gated fixes (spec F5, F7, F14).
#
#   nightwatch-incident ─► RequestApproval ──► nightwatch-report posts the card, parks the token
#                          (waits, free)    ◄── nightwatch-interactions: a Discord button click
#                          Remediate ───────► nightwatch-remediate (remediator role)
#                          Verify ──────────► nightwatch-verify (read-only investigator role)
#
# The remediator is the only role that can change the watched service, and only through the
# calls in nightwatch/remediation/runbooks.py ALLOWED_CALLS, on the watched resources.

locals {
  remediate_name    = "nightwatch-remediate"
  verify_name       = "nightwatch-verify"
  interactions_name = "nightwatch-interactions"
  kill_switch_arn   = "arn:aws:ssm:${local.region}:${local.account}:parameter${var.kill_switch_param}"
  watched_functions = flatten([for arn in var.watched.function_arns : [arn, "${arn}:*"]])
}

# Spec F14. Terraform only sets the first value; flip it with
#   aws ssm put-parameter --name /nightwatch/kill_switch --value true --overwrite
resource "aws_ssm_parameter" "kill_switch" {
  #checkov:skip=CKV2_AWS_34:An on/off switch is not secret; SecureString would add KMS calls for nothing
  name        = var.kill_switch_param
  description = "true stops every NightWatch fix (spec F14). Anything but false counts as true."
  type        = "String"
  value       = "false"
  tags        = var.tags

  lifecycle {
    ignore_changes = [value]
  }
}

# ----------------------------------------------------------------------- remediate

resource "aws_cloudwatch_log_group" "remediate" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.remediate_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "remediate" {
  name                 = local.remediate_name
  path                 = "/nightwatch/"
  description          = "Remediator (spec section 11): only the runbooks' allow-listed calls, only on watched resources"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  tags                 = var.tags
}

resource "aws_iam_role_policy" "remediate" {
  name   = local.remediate_name
  role   = aws_iam_role.remediate.id
  policy = data.aws_iam_policy_document.remediate.json
}

data "aws_iam_policy_document" "remediate" {
  statement {
    sid       = "NightWatchState"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.this.arn]
  }

  statement {
    sid       = "ReadKillSwitch"
    actions   = ["ssm:GetParameter"]
    resources = [local.kill_switch_arn]
  }

  # One statement per allow-listed call, each with the read that checks the state first.
  statement {
    sid       = "RollBackAlias"
    actions   = ["lambda:GetAlias", "lambda:UpdateAlias"]
    resources = local.watched_functions
  }

  statement {
    sid       = "ResetConcurrency"
    actions   = ["lambda:GetFunctionConcurrency", "lambda:DeleteFunctionConcurrency"]
    resources = var.watched.function_arns
  }

  statement {
    sid       = "ResetFlags"
    actions   = ["ssm:GetParameter", "ssm:PutParameter"]
    resources = var.watched.parameter_arns
  }

  statement {
    sid       = "RestoreCapacity"
    actions   = ["dynamodb:DescribeTable", "dynamodb:UpdateTable"]
    resources = var.watched.table_arns
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.remediate.arn}:*"]
  }
}

resource "aws_lambda_function" "remediate" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed; the audit log records every change
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:Invoked synchronously by Step Functions, which catches failures
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.remediate_name
  description      = "Runs one human-approved runbook action, then records it (spec F5)"
  role             = aws_iam_role.remediate.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.remediation.remediate.handler"
  memory_size      = 256
  timeout          = 30
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      TABLE_NAME        = aws_dynamodb_table.this.name
      TENANT_ID         = var.tenant_id
      KILL_SWITCH_PARAM = var.kill_switch_param
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.remediate.name
  }

  tags = var.tags
}

# -------------------------------------------------------------------------- verify
# Uses the read-only investigator role: verifying is reading metrics.

resource "aws_cloudwatch_log_group" "verify" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.verify_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "verify" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed; the audit log records every verdict
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:Invoked synchronously by Step Functions, which catches failures
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.verify_name
  description      = "Checks the runbook's metric after a fix (spec F7)"
  role             = aws_iam_role.investigate.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.remediation.verify.handler"
  memory_size      = 256
  timeout          = 30
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.this.name
      TENANT_ID  = var.tenant_id
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.verify.name
  }

  tags = var.tags
}

# -------------------------------------------------------------------- interactions
# Discord sends button clicks here. The URL is public because Discord can't sign AWS
# requests; the function itself rejects anything without a valid Ed25519 signature (ADR 0012).

resource "aws_cloudwatch_log_group" "interactions" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.interactions_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "interactions" {
  name                 = local.interactions_name
  path                 = "/nightwatch/"
  description          = "Discord button clicks: records the decision and resumes the workflow"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  tags                 = var.tags
}

resource "aws_iam_role_policy" "interactions" {
  name   = local.interactions_name
  role   = aws_iam_role.interactions.id
  policy = data.aws_iam_policy_document.interactions.json
}

data "aws_iam_policy_document" "interactions" {
  statement {
    sid       = "NightWatchState"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.this.arn, "${aws_dynamodb_table.this.arn}/index/*"]
  }

  statement {
    sid       = "StartAsk"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.ask.arn]
  }

  statement {
    # Checked against AWS's service authorization reference on 2026-10-29: the task-token
    # calls have no resource-level permissions. The token itself names the execution.
    sid       = "ResumeWorkflow"
    actions   = ["states:SendTaskSuccess"]
    resources = ["*"]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.interactions.arn}:*"]
  }
}

resource "aws_lambda_function" "interactions" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed; the audit log records every decision
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:Called synchronously through its URL; Discord shows any failure to the clicker
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold ids and a public key only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.interactions_name
  description      = "Verifies Discord button clicks and resumes the approval wait (spec F5)"
  role             = aws_iam_role.interactions.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.chat.interactions.handler"
  memory_size      = 512 # more memory means more CPU: Discord wants an answer in 3 seconds
  timeout          = 10
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      TABLE_NAME         = aws_dynamodb_table.this.name
      TENANT_ID          = var.tenant_id
      DISCORD_PUBLIC_KEY = var.discord_public_key
      ASK_FUNCTION       = aws_lambda_function.ask.function_name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.interactions.name
  }

  tags = var.tags
}

resource "aws_lambda_function_url" "interactions" {
  #checkov:skip=CKV_AWS_258:Discord can't sign AWS requests; every request's Ed25519 signature is checked in code (ADR 0012)
  function_name      = aws_lambda_function.interactions.function_name
  authorization_type = "NONE"
}

# Public URLs need both permissions since October 2025; the second only works through the URL.
resource "aws_lambda_permission" "interactions_url" {
  #checkov:skip=CKV_AWS_301:Public by design for Discord; requests without a valid signature get 401 (ADR 0012)
  statement_id           = "DiscordCallsTheUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.interactions.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "interactions_invoke" {
  #checkov:skip=CKV_AWS_301:Only through the function URL (invoked_via_function_url), which checks signatures
  statement_id             = "OnlyThroughTheUrl"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.interactions.function_name
  principal                = "*"
  invoked_via_function_url = true
}
