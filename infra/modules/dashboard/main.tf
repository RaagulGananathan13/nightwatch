# Phase 7: the web dashboard (spec F18).
#
#   browser ──https──► CloudFront ─┬─ /*      ─► S3 (the React app; private, read through OAC)
#                                  └─ /api/*  ─► nightwatch-api Lambda (function URL, AWS_IAM:
#                                                only CloudFront can call it, signed with OAC)
#   sign-in: Cognito's hosted login page (authorization code + PKCE); the API checks the token.
#
# All of it is inside AWS's always-free allowances: CloudFront (1 TB and 10M requests a month),
# Cognito Lite (10,000 monthly users), Lambda. S3 holds a few hundred KB.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account  = data.aws_caller_identity.current.account_id
  region   = data.aws_region.current.region
  api_name = "nightwatch-api"
  domain   = "nightwatch-${local.account}" # Cognito's hosted login page prefix
  login    = "${local.domain}.auth.${local.region}.amazoncognito.com"
  site_url = "https://${aws_cloudfront_distribution.dashboard.domain_name}/"
  # A fixed name, not a reference: see aws_ssm_parameter.client_id.
  client_param = "/nightwatch/dashboard/client_id"
}

# ------------------------------------------------------------------- the React app

resource "aws_s3_bucket" "site" {
  #checkov:skip=CKV_AWS_18:Access logging needs a second bucket and paid storage (ADR 0007)
  #checkov:skip=CKV_AWS_144:Cross-region replication doubles storage cost; CI can redeploy the site
  #checkov:skip=CKV_AWS_145:Customer-managed KMS keys cost $1/month; SSE-S3 is free (ADR 0007)
  #checkov:skip=CKV2_AWS_62:Nothing needs to react to new site files
  bucket = "nightwatch-${local.account}-dashboard"
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    id     = "tidy-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site.json
}

data "aws_iam_policy_document" "site" {
  statement {
    sid       = "OnlyThisDistributionReads"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.dashboard.arn]
    }
  }
}

# The browser reads this to find the login page; nothing in it is secret.
resource "aws_s3_object" "config" {
  bucket        = aws_s3_bucket.site.id
  key           = "config.json"
  content_type  = "application/json"
  cache_control = "no-cache"
  content = jsonencode({
    region     = local.region
    userPoolId = aws_cognito_user_pool.dashboard.id
    clientId   = aws_cognito_user_pool_client.dashboard.id
    domain     = local.login
  })
}

# ------------------------------------------------------------------------- sign-in

resource "aws_cognito_user_pool" "dashboard" {
  name                = "nightwatch-dashboard"
  user_pool_tier      = "LITE" # 10,000 monthly active users are free
  username_attributes = ["email"]
  mfa_configuration   = "OPTIONAL"
  deletion_protection = "INACTIVE" # `make destroy` must be able to remove everything

  software_token_mfa_configuration {
    enabled = true
  }

  admin_create_user_config {
    allow_admin_create_user_only = true # no self sign-up: Terraform adds each person

    invite_message_template {
      email_subject = "Your NightWatch dashboard login"
      email_message = "Sign in to the NightWatch dashboard as {username} with the temporary password {####}. You'll choose your own password the first time."
      sms_message   = "NightWatch dashboard: {username} / {####}"
    }
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  tags = var.tags
}

resource "aws_cognito_user_pool_domain" "dashboard" {
  domain                = local.domain
  user_pool_id          = aws_cognito_user_pool.dashboard.id
  managed_login_version = 1 # the classic hosted login page, which needs no branding setup
}

resource "aws_cognito_user_pool_client" "dashboard" {
  name                                 = "nightwatch-dashboard"
  user_pool_id                         = aws_cognito_user_pool.dashboard.id
  generate_secret                      = false # a browser app can't keep a secret; PKCE instead
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = [local.site_url]
  logout_urls                          = [local.site_url]
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 12

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }
}

# Cognito emails each person a temporary password. Indexed by position, not by address,
# because the addresses are sensitive and resource keys show up in public CI logs.
resource "aws_cognito_user" "people" {
  count        = nonsensitive(length(var.users))
  user_pool_id = aws_cognito_user_pool.dashboard.id
  username     = var.users[count.index]

  attributes = {
    email          = var.users[count.index]
    email_verified = true
  }

  desired_delivery_mediums = ["EMAIL"]

  lifecycle {
    ignore_changes = [attributes]
  }
}

# The API reads the app client's id from here, which breaks a dependency loop:
# app client -> CloudFront address -> API's function URL -> API.
resource "aws_ssm_parameter" "client_id" {
  #checkov:skip=CKV2_AWS_34:An app client id is public (it is in config.json too)
  name  = local.client_param
  type  = "String"
  value = aws_cognito_user_pool_client.dashboard.id
  tags  = var.tags
}

# ----------------------------------------------------------------------------- API

resource "aws_cloudwatch_log_group" "api" {
  #checkov:skip=CKV_AWS_158:Customer-managed KMS keys cost $1/month (ADR 0007)
  #checkov:skip=CKV_AWS_338:Short retention keeps log storage inside the free tier (ADR 0007)
  name              = "/aws/lambda/${local.api_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role" "api" {
  name                 = local.api_name
  path                 = "/nightwatch/"
  description          = "Dashboard API: reads NightWatch's incidents and postmortems, writes nothing"
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
  name   = local.api_name
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ReadNightWatchState"
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }

  statement {
    sid       = "ReadPostmortems"
    actions   = ["s3:GetObject"]
    resources = ["${var.postmortem_bucket_arn}/postmortems/*"]
  }

  statement {
    sid     = "ReadSettings"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${local.region}:${local.account}:parameter${local.client_param}",
      "arn:aws:ssm:${local.region}:${local.account}:parameter${var.kill_switch_param}",
    ]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }
}

resource "aws_lambda_function" "api" {
  #checkov:skip=CKV_AWS_50:X-Ray isn't needed for a read-only API
  #checkov:skip=CKV_AWS_115:New AWS accounts can't reserve concurrency (account limit of 10)
  #checkov:skip=CKV_AWS_116:Called synchronously through CloudFront; the browser shows any error
  #checkov:skip=CKV_AWS_117:A VPC would need a NAT gateway, which is not free (ADR 0007)
  #checkov:skip=CKV_AWS_173:Environment variables hold names and ids only, no secrets
  #checkov:skip=CKV_AWS_272:Code signing is a paid workflow feature for this use
  function_name    = local.api_name
  description      = "The dashboard's read-only API (spec F18)"
  role             = aws_iam_role.api.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "nightwatch.api.handler.handler"
  memory_size      = 512
  timeout          = 15
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  layers           = [var.layer_arn]

  environment {
    variables = {
      TABLE_NAME             = var.table_name
      TENANT_ID              = var.tenant_id
      POSTMORTEM_BUCKET      = var.postmortem_bucket
      KILL_SWITCH_PARAM      = var.kill_switch_param
      DASHBOARD_POOL_ID      = aws_cognito_user_pool.dashboard.id
      DASHBOARD_CLIENT_PARAM = local.client_param
      LLM_PROVIDERS          = var.llm_providers
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.api.name
  }

  tags = var.tags
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "AWS_IAM" # only CloudFront, signing with its origin access control
}

resource "aws_lambda_permission" "cloudfront_url" {
  statement_id  = "CloudFrontCallsTheUrl"
  action        = "lambda:InvokeFunctionUrl"
  function_name = aws_lambda_function.api.function_name
  principal     = "cloudfront.amazonaws.com"
  source_arn    = aws_cloudfront_distribution.dashboard.arn
}

resource "aws_lambda_permission" "cloudfront_invoke" {
  statement_id             = "CloudFrontOnlyThroughTheUrl"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api.function_name
  principal                = "cloudfront.amazonaws.com"
  source_arn               = aws_cloudfront_distribution.dashboard.arn
  invoked_via_function_url = true
}

# ---------------------------------------------------------------------- CloudFront

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "nightwatch-dashboard-site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_origin_access_control" "api" {
  name                              = "nightwatch-dashboard-api"
  origin_access_control_origin_type = "lambda"
  signing_behavior                  = "always" # so the browser's token goes in its own header
  signing_protocol                  = "sigv4"
}

data "aws_cloudfront_cache_policy" "static" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "none" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "api" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_response_headers_policy" "dashboard" {
  name    = "nightwatch-dashboard"
  comment = "Security headers for the NightWatch dashboard"

  security_headers_config {
    content_security_policy {
      override = true
      content_security_policy = join("; ", [
        "default-src 'self'",
        "connect-src 'self' https://${local.login}",
        "img-src 'self' data:",
        "style-src 'self'",
        "script-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self' https://${local.login}",
      ])
    }

    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "same-origin"
      override        = true
    }

    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
  }
}

resource "aws_cloudfront_distribution" "dashboard" {
  #checkov:skip=CKV_AWS_68:AWS WAF costs at least $5 a month; the API checks every token and the site is static
  #checkov:skip=CKV2_AWS_47:AWS WAF costs at least $5 a month (see CKV_AWS_68)
  #checkov:skip=CKV_AWS_86:Access logs need a paid log bucket; CloudFront's free metrics are enough here
  #checkov:skip=CKV_AWS_174:TLS 1.2 minimum needs a custom domain and certificate; the default cloudfront.net certificate is used
  #checkov:skip=CKV2_AWS_42:No custom domain, so the default CloudFront certificate is used
  #checkov:skip=CKV_AWS_310:There is one copy of each origin; a failover origin would be a second paid copy
  #checkov:skip=CKV_AWS_374:The on-call team can be anywhere, so no geo restriction
  enabled             = true
  comment             = "NightWatch dashboard"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  http_version        = "http2and3"
  is_ipv6_enabled     = true

  origin {
    origin_id                = "site"
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  origin {
    origin_id                = "api"
    domain_name              = trimsuffix(trimprefix(aws_lambda_function_url.api.function_url, "https://"), "/")
    origin_access_control_id = aws_cloudfront_origin_access_control.api.id

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id           = "site"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.static.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.dashboard.id
  }

  ordered_cache_behavior {
    path_pattern               = "/api/*"
    target_origin_id           = "api"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.none.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.api.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.dashboard.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = var.tags
}
