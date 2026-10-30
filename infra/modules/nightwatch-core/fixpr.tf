# Phase 8, F10: fix-as-pull-request. When the evidence shows a Terraform setting is simply too
# small, this Lambda opens a GitHub pull request raising it. People review, merge and apply.
#
# Its only write outside NightWatch's table is to GitHub, with a fine-grained token you create
# for this one repository (Contents + Pull requests) and store at var.github_token_param.

locals {
  fixpr_name = "nightwatch-fixpr"
}

resource "aws_cloudwatch_log_group" "fixpr" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.fixpr_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "fixpr" {
  name                 = local.fixpr_name
  path                 = "/nightwatch/"
  description          = "Opens Terraform fix pull requests on GitHub; no AWS changes"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  tags                 = var.tags
}

resource "aws_iam_role_policy" "fixpr" {
  name   = local.fixpr_name
  role   = aws_iam_role.fixpr.id
  policy = data.aws_iam_policy_document.fixpr.json
}

data "aws_iam_policy_document" "fixpr" {
  statement {
    sid       = "NightWatchState"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.this.arn]
  }

  statement {
    sid     = "TokenWebhookAndSwitch"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${local.region}:${local.account}:parameter${var.github_token_param}",
      "arn:aws:ssm:${local.region}:${local.account}:parameter${var.discord_webhook_param}",
      "arn:aws:ssm:${local.region}:${local.account}:parameter/nightwatch/dashboard/url",
      local.kill_switch_arn,
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
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.fixpr.arn}:*"]
  }
}

resource "aws_lambda_function" "fixpr" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed; the audit log records every pull request
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:Invoked synchronously by Step Functions, which catches failures
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.fixpr_name
  description      = "Opens a Terraform fix pull request when a setting is too small (spec F10)"
  role             = aws_iam_role.fixpr.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.remediation.fixpr.handler"
  memory_size      = 256
  timeout          = 45
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      TABLE_NAME            = aws_dynamodb_table.this.name
      TENANT_ID             = var.tenant_id
      GITHUB_TOKEN_PARAM    = var.github_token_param
      DISCORD_WEBHOOK_PARAM = var.discord_webhook_param
      KILL_SWITCH_PARAM     = var.kill_switch_param
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.fixpr.name
  }

  tags = var.tags
}
