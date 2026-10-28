# ShopLite: the demo store NightWatch watches. Everything here stays inside AWS always-free limits.
#
# Code ownership is split on purpose (ADR 0008):
#   - Terraform creates the function, its first version and the `live` alias.
#   - GitHub Actions ships every later code change as a new version and moves the alias, so each
#     deploy is a real, timestamped "what changed?" event with the git SHA in its description.

locals {
  api_name    = "shoplite-api"
  canary_name = "shoplite-canary"

  # Tags NightWatch reads: which service a resource belongs to, and that runbooks may touch it.
  managed_tags = merge(var.tags, {
    "nightwatch:service" = "shoplite"
    "nightwatch:managed" = "true"
  })

  products = {
    "p-100" = { name = "Coffee mug", price_cents = 1200 }
    "p-200" = { name = "Notebook", price_cents = 650 }
    "p-300" = { name = "Desk lamp", price_cents = 3400 }
    "p-400" = { name = "Water bottle", price_cents = 1800 }
    "p-500" = { name = "Sticker pack", price_cents = 300 }
  }
}

data "aws_caller_identity" "current" {}

# ----------------------------------------------------------------------------- data

resource "aws_dynamodb_table" "this" {
  #checkov:skip=CKV_AWS_28:Point-in-time recovery is billed per GB-month; demo data is re-seeded by Terraform (ADR 0007)
  #checkov:skip=CKV_AWS_119:Customer-managed KMS keys cost $1/month; the AWS-owned key is free (ADR 0007)
  #checkov:skip=CKV2_AWS_16:Auto scaling could push capacity past the 25-unit free tier (ADR 0002)
  name           = "shoplite"
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

  tags = local.managed_tags
}

resource "aws_dynamodb_table_item" "product" {
  for_each   = local.products
  table_name = aws_dynamodb_table.this.name
  hash_key   = aws_dynamodb_table.this.hash_key
  range_key  = aws_dynamodb_table.this.range_key

  item = jsonencode({
    PK          = { S = "PRODUCT" }
    SK          = { S = "PRODUCT#${each.key}" }
    name        = { S = each.value.name }
    price_cents = { N = tostring(each.value.price_cents) }
  })
}

# Feature flags the chaos lab flips (see shoplite/flags.py). Not secret, so a plain String.
resource "aws_ssm_parameter" "flags" {
  #checkov:skip=CKV2_AWS_34:Feature flags are not secret; SecureString would add KMS calls for nothing
  name        = "/shoplite/flags"
  description = "ShopLite feature flags (JSON). The chaos lab changes these to inject failures."
  type        = "String"
  value       = "{}"
  tags        = local.managed_tags

  lifecycle {
    # Chaos scenarios and runbooks change this at runtime; Terraform shouldn't fight them.
    ignore_changes = [value]
  }
}

# ------------------------------------------------------------------------------ api

resource "aws_cloudwatch_log_group" "api" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.api_name}"
  retention_in_days = var.log_retention_days
  tags              = local.managed_tags
}

resource "aws_iam_role" "api" {
  name                 = local.api_name
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

resource "aws_iam_role_policy" "api" {
  name   = "shoplite-api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ReadWriteStore"
    actions   = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.this.arn]
  }

  statement {
    sid       = "ReadFlags"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.flags.arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }
}

resource "aws_lambda_function" "api" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed; logs and metrics are NightWatch's evidence
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:Called synchronously through its function URL, so a DLQ never fires
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold no secrets; the AWS-managed key is free
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name = local.api_name
  description   = "ShopLite store API (demo workload watched by NightWatch)"
  role          = aws_iam_role.api.arn
  runtime       = "python3.13"
  architectures = ["arm64"]
  handler       = "shoplite.app.handler"
  memory_size   = 256
  timeout       = 5
  filename      = var.package_path
  publish       = true

  environment {
    variables = {
      TABLE_NAME  = aws_dynamodb_table.this.name
      FLAGS_PARAM = aws_ssm_parameter.flags.name
    }
  }

  logging_config {
    log_format = "Text" # the app already writes one JSON line per request
    log_group  = aws_cloudwatch_log_group.api.name
  }

  tags = local.managed_tags

  lifecycle {
    # CI owns the code after the first deploy (scripts/deploy_lambda.py).
    ignore_changes = [filename, source_code_hash]
  }
}

resource "aws_lambda_alias" "live" {
  name             = "live"
  description      = "The version customers get. CI and rollback runbooks move this."
  function_name    = aws_lambda_function.api.function_name
  function_version = aws_lambda_function.api.version

  lifecycle {
    ignore_changes = [function_version]
  }
}

# AWS_IAM auth: unsigned requests are refused before the function runs, so nobody can spam
# the endpoint into the paid tier (ADR 0001).
resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  qualifier          = aws_lambda_alias.live.name
  authorization_type = "AWS_IAM"
}

# --------------------------------------------------------------------------- canary

data "archive_file" "canary" {
  type             = "zip"
  source_file      = "${path.module}/../../../shoplite/canary.py"
  output_path      = "${path.root}/.build/shoplite-canary.zip"
  output_file_mode = "0644"
}

resource "aws_cloudwatch_log_group" "canary" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.canary_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "canary" {
  name                 = local.canary_name
  path                 = "/nightwatch/"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  tags                 = var.tags
}

resource "aws_iam_role_policy" "canary" {
  name   = "shoplite-canary"
  role   = aws_iam_role.canary.id
  policy = data.aws_iam_policy_document.canary.json
}

data "aws_iam_policy_document" "canary" {
  statement {
    sid       = "CallShopLite"
    actions   = ["lambda:InvokeFunctionUrl", "lambda:InvokeFunction"]
    resources = [aws_lambda_alias.live.arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.canary.arn}:*"]
  }
}

resource "aws_lambda_function" "canary" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed for a traffic generator
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:A missed canary run needs no retry
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold no secrets; the AWS-managed key is free
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.canary_name
  description      = "Steady signed traffic to ShopLite so alarms and baselines have data"
  role             = aws_iam_role.canary.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "canary.handler"
  memory_size      = 128
  timeout          = 30
  filename         = data.archive_file.canary.output_path
  source_code_hash = data.archive_file.canary.output_base64sha256

  environment {
    variables = {
      SHOPLITE_URL = aws_lambda_function_url.api.function_url
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.canary.name
  }

  tags = var.tags
}

# EventBridge Scheduler: 14 million invocations a month are free; this uses about 44,000.
resource "aws_scheduler_schedule" "canary" {
  count                        = var.canary_enabled ? 1 : 0
  name                         = local.canary_name
  description                  = "Run the ShopLite canary"
  schedule_expression          = var.canary_schedule
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.canary.arn
    role_arn = aws_iam_role.scheduler[0].arn

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

resource "aws_iam_role" "scheduler" {
  count                = var.canary_enabled ? 1 : 0
  name                 = "shoplite-canary-scheduler"
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
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role_policy" "scheduler" {
  count  = var.canary_enabled ? 1 : 0
  name   = "invoke-canary"
  role   = aws_iam_role.scheduler[0].id
  policy = data.aws_iam_policy_document.scheduler.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.canary.arn]
  }
}
