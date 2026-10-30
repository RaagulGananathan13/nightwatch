# Phase 6: postmortems, incident memory and /nw ask (spec F6, F8, F9).
#
#   ingest (incident resolved) ──┐
#   report (fix finished) ───────┴─► nightwatch-postmortem ─► S3 postmortems/<tenant>/INC-n.md
#                                                            └► memory item in DynamoDB
#   Discord /nw ask ─► nightwatch-interactions ─► nightwatch-ask (read-only investigator role)

locals {
  postmortem_name = "nightwatch-postmortem"
  ask_name        = "nightwatch-ask"
}

# ------------------------------------------------------------------ the S3 bucket
# Postmortems are a few KB each: storage and requests cost fractions of a cent a month.

resource "aws_s3_bucket" "postmortems" {
  #checkov:skip=CKV_AWS_18:Access logging needs a second bucket and paid storage for KB-size reports (ADR 0007)
  #checkov:skip=CKV_AWS_144:Cross-region replication doubles storage cost; versioning covers mistakes
  #checkov:skip=CKV_AWS_145:Customer-managed KMS keys cost $1/month; SSE-S3 is free (ADR 0007)
  #checkov:skip=CKV2_AWS_62:Nothing needs to react to new postmortems
  bucket = "nightwatch-${local.account}-postmortems"
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "postmortems" {
  bucket = aws_s3_bucket.postmortems.id

  versioning_configuration {
    status = "Enabled" # a rewritten postmortem keeps the earlier version
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "postmortems" {
  bucket = aws_s3_bucket.postmortems.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "postmortems" {
  bucket                  = aws_s3_bucket.postmortems.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "postmortems" {
  bucket = aws_s3_bucket.postmortems.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "postmortems" {
  bucket = aws_s3_bucket.postmortems.id

  rule {
    id     = "tidy-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# --------------------------------------------------------------------- postmortem

resource "aws_cloudwatch_log_group" "postmortem" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.postmortem_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "postmortem" {
  name                 = local.postmortem_name
  path                 = "/nightwatch/"
  description          = "Writes postmortems to S3 and incident memories to DynamoDB"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  tags                 = var.tags
}

resource "aws_iam_role_policy" "postmortem" {
  name   = local.postmortem_name
  role   = aws_iam_role.postmortem.id
  policy = data.aws_iam_policy_document.postmortem.json
}

data "aws_iam_policy_document" "postmortem" {
  statement {
    sid       = "NightWatchState"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.this.arn]
  }

  statement {
    sid       = "WritePostmortems"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.postmortems.arn}/postmortems/*"]
  }

  statement {
    sid     = "LlmKeysAndWebhook"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${local.region}:${local.account}:parameter/nightwatch/llm/*",
      "arn:aws:ssm:${local.region}:${local.account}:parameter${var.discord_webhook_param}",
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
    resources = ["${aws_cloudwatch_log_group.postmortem.arn}:*"]
  }
}

resource "aws_lambda_function" "postmortem" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed; the audit log records every postmortem
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:A failed postmortem is rewritten on the next resolve or by make backfill-memory
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.postmortem_name
  description      = "Writes a blameless postmortem and the incident's memory (spec F6, F8)"
  role             = aws_iam_role.postmortem.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.postmortem.handler.handler"
  memory_size      = 256
  timeout          = 60
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      TABLE_NAME            = aws_dynamodb_table.this.name
      TENANT_ID             = var.tenant_id
      POSTMORTEM_BUCKET     = aws_s3_bucket.postmortems.bucket
      DISCORD_WEBHOOK_PARAM = var.discord_webhook_param
      LLM_PROVIDERS         = var.llm_providers
      LLM_DAILY_REQUESTS    = tostring(var.llm_daily_requests)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.postmortem.name
  }

  tags = var.tags
}

# ----------------------------------------------------------------------------- ask
# Same code and read-only role as the investigation; only the entry point differs.

resource "aws_cloudwatch_log_group" "ask" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.ask_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "ask" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed; the evidence ledger and audit log are the trace
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:A failed answer is reported in Discord; the asker can ask again
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.ask_name
  description      = "Answers /nw ask questions with a read-only investigation (spec F9)"
  role             = aws_iam_role.investigate.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.agent.ask.handler"
  memory_size      = 512
  timeout          = 120
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      TABLE_NAME         = aws_dynamodb_table.this.name
      TENANT_ID          = var.tenant_id
      LLM_PROVIDERS      = var.llm_providers
      LLM_DAILY_REQUESTS = tostring(var.llm_daily_requests)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ask.name
  }

  tags = var.tags
}
