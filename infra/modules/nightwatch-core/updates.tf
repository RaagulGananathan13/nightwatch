# Phase 8, F13: stakeholder updates and the public status page.
#
#   Discord /nw update ─► nightwatch-interactions ─► nightwatch-updates (drafts both updates)
#   Discord "Publish"  ─► nightwatch-interactions ─► S3 status/index.html ─► CloudFront
#
# The status page is one static file in the dashboard's bucket, served by the dashboard's
# CloudFront distribution: nothing new to pay for. Drafting is one LLM call under the
# investigator's read-only role; only an approver's click publishes.

locals {
  updates_name = "nightwatch-updates"
  # Created by the dashboard module, which depends on this one: named here, not referenced.
  status_bucket = "nightwatch-${local.account}-dashboard"
}

resource "aws_cloudwatch_log_group" "updates" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.updates_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "updates" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed; the audit log records every draft and decision
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:A failed draft is reported in Discord; the asker can ask again
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.updates_name
  description      = "Drafts a customer update and a leadership summary (spec F13)"
  role             = aws_iam_role.investigate.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.status.handler.handler"
  memory_size      = 256
  timeout          = 60
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
    log_group  = aws_cloudwatch_log_group.updates.name
  }

  tags = var.tags
}
