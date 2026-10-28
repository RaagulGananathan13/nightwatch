# One-time setup: the S3 bucket that stores Terraform state for every NightWatch environment.
#
# This stack keeps its own (tiny) state in a local terraform.tfstate file, which git ignores.
# If that file is ever lost, adopt the bucket again with:
#   terraform import aws_s3_bucket.state nightwatch-<account-id>-tfstate

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project      = "nightwatch"
      "managed-by" = "terraform"
      stack        = "bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  bucket_name = "nightwatch-${data.aws_caller_identity.current.account_id}-tfstate"
}

resource "aws_s3_bucket" "state" {
  #checkov:skip=CKV_AWS_18:Access logging needs a second bucket and paid storage for KB-size state (ADR 0007)
  #checkov:skip=CKV_AWS_144:Cross-region replication doubles storage cost; versioning covers mistakes (ADR 0004)
  #checkov:skip=CKV_AWS_145:Customer-managed KMS keys cost $1/month; SSE-S3 is free (ADR 0007)
  #checkov:skip=CKV2_AWS_62:Nothing needs to react to state-file events
  bucket = local.bucket_name
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Old state versions are only useful for a few weeks of "undo"; after that they are just cost.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
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

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

data "aws_iam_policy_document" "tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}
