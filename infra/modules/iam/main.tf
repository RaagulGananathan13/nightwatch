# GitHub Actions reaches AWS through OIDC: short-lived tokens, no access keys stored in GitHub.
#
#   github-deploy  ships ShopLite code. Only jobs that use the repo's `dev` environment can
#                  assume it, and that environment only deploys from main.
#   github-plan    read-only, runs `terraform plan` on pull requests from this repo. Pull
#                  requests from forks get no OIDC token at all.
#
# Both roles carry the NightWatchBoundary permissions boundary like every other role.
#
# GitHub signs tokens with an *immutable* subject that includes the owner and repository IDs,
# e.g. repo:owner@123/name@456:environment:dev. Trusting only that form means a repository
# re-created under the same name later can never inherit this access.

locals {
  oidc_host         = "token.actions.githubusercontent.com"
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
  tags           = var.tags
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://${local.oidc_host}"
}

data "aws_iam_policy_document" "trust" {
  for_each = {
    deploy = "${var.github_subject_prefix}:environment:${var.deploy_environment}"
    plan   = "${var.github_subject_prefix}:pull_request"
  }

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = [each.value]
    }
  }
}

# --------------------------------------------------------------------------- deploy

resource "aws_iam_role" "deploy" {
  name                 = "github-deploy"
  path                 = "/nightwatch/"
  description          = "GitHub Actions: deploy Lambda code from main"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.trust["deploy"].json
  max_session_duration = 3600
  tags                 = var.tags
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy-lambda-code"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid = "ShipNewVersions"
    actions = [
      "lambda:UpdateFunctionCode",
      "lambda:PublishVersion",
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:GetAlias",
      "lambda:UpdateAlias",
    ]
    resources = flatten([for arn in var.deployable_function_arns : [arn, "${arn}:*"]])
  }

  statement {
    sid       = "SmokeTest"
    actions   = ["lambda:InvokeFunctionUrl", "lambda:InvokeFunction"]
    resources = [for arn in var.deployable_function_arns : "${arn}:live"]
  }

  statement {
    sid       = "UploadDashboard"
    actions   = ["s3:ListBucket", "s3:PutObject", "s3:DeleteObject"]
    resources = [var.dashboard.bucket_arn, "${var.dashboard.bucket_arn}/*"]
  }

  statement {
    sid       = "RefreshDashboardCache"
    actions   = ["cloudfront:CreateInvalidation"]
    resources = [var.dashboard.distribution_arn]
  }
}

# ----------------------------------------------------------------------------- plan

resource "aws_iam_role" "plan" {
  name                 = "github-plan"
  path                 = "/nightwatch/"
  description          = "GitHub Actions: read-only terraform plan on pull requests"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.trust["plan"].json
  max_session_duration = 3600
  tags                 = var.tags
}

resource "aws_iam_role_policy" "plan" {
  name   = "read-only-plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}

locals {
  account = data.aws_caller_identity.current.account_id
  region  = data.aws_region.current.region
  # Name prefixes NightWatch owns, the same ones the NightWatchBoundary policy allows.
  owned = ["nightwatch", "shoplite"]
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_iam_policy_document" "plan" {
  # Reads only, and only on NightWatch's own resources (no "*" on anything that holds data).
  statement {
    sid = "ReadOwnResources"
    actions = [
      "budgets:ListTagsForResource",
      "budgets:ViewBudget",
      "cloudwatch:DescribeAlarms",
      "cloudwatch:ListTagsForResource",
      "dynamodb:Describe*",
      "dynamodb:GetItem", # seeded ShopLite products are Terraform resources too
      "dynamodb:ListTagsOfResource",
      "events:DescribeRule",
      "events:ListTagsForResource",
      "events:ListTargetsByRule",
      "iam:GetOpenIDConnectProvider",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListRolePolicies",
      "lambda:Get*",
      "lambda:ListTags",
      "lambda:ListVersionsByFunction",
      "logs:ListTagsForResource",
      "scheduler:GetSchedule",
      "sqs:GetQueueAttributes",
      "sqs:ListQueueTags",
      "ssm:GetParameter",
      "ssm:ListTagsForResource",
      "states:Describe*",
      "states:ListTagsForResource",
    ]
    resources = concat(
      flatten([for p in local.owned : [
        "arn:aws:lambda:${local.region}:${local.account}:function:${p}-*",
        "arn:aws:dynamodb:${local.region}:${local.account}:table/${p}*",
        "arn:aws:ssm:${local.region}:${local.account}:parameter/${p}/*",
        "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/lambda/${p}-*",
        "arn:aws:cloudwatch:${local.region}:${local.account}:alarm:${p}-*",
        "arn:aws:scheduler:${local.region}:${local.account}:schedule/*/${p}-*",
      ]]),
      [
        "arn:aws:events:${local.region}:${local.account}:rule/nightwatch-*",
        "arn:aws:states:${local.region}:${local.account}:stateMachine:nightwatch-*",
        "arn:aws:iam::${local.account}:role/nightwatch/*",
        "arn:aws:iam::${local.account}:oidc-provider/${local.oidc_host}",
        "arn:aws:budgets::${local.account}:budget/nightwatch-*",
        "arn:aws:lambda:${local.region}:${local.account}:layer:nightwatch-*",
        "arn:aws:sqs:${local.region}:${local.account}:nightwatch-*",
      ],
    )
  }

  statement {
    sid       = "ListLogGroups"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:${local.region}:${local.account}:log-group:*"]
  }

  statement {
    sid       = "ListParameters" # this API has no resource-level permissions
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]
  }

  statement {
    sid       = "ReadState"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [var.state_bucket_arn, "${var.state_bucket_arn}/*"]
  }

  statement {
    # The postmortem and dashboard buckets' settings (Terraform reads them all in a plan).
    sid     = "ReadOwnBuckets"
    actions = ["s3:GetBucket*", "s3:GetLifecycleConfiguration", "s3:GetEncryptionConfiguration", "s3:GetObject", "s3:ListBucket", "s3:GetObjectTagging", "s3:GetAccelerateConfiguration", "s3:GetReplicationConfiguration"]
    resources = [
      "arn:aws:s3:::nightwatch-${local.account}-*",
      "arn:aws:s3:::nightwatch-${local.account}-*/*",
    ]
  }

  statement {
    sid       = "ReadDashboardCdn" # CloudFront and its managed policies are account-wide
    actions   = ["cloudfront:Get*", "cloudfront:List*"]
    resources = ["*"]
  }

  statement {
    sid       = "ReadDashboardLogin"
    actions   = ["cognito-idp:Describe*", "cognito-idp:Get*", "cognito-idp:List*", "cognito-idp:AdminGetUser"]
    resources = ["arn:aws:cognito-idp:${local.region}:${local.account}:userpool/*"]
  }

  statement {
    sid       = "ReadLoginDomain" # this API has no resource-level permissions
    actions   = ["cognito-idp:DescribeUserPoolDomain"]
    resources = ["*"]
  }
}
