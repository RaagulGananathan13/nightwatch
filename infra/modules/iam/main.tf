# GitHub Actions reaches AWS through OIDC: short-lived tokens, no access keys stored in GitHub.
#
#   github-deploy  ships ShopLite code. Only jobs that use the repo's `dev` environment can
#                  assume it, and that environment only deploys from main.
#   github-plan    read-only, runs `terraform plan` on pull requests from this repo. Pull
#                  requests from forks get no OIDC token at all.
#
# Both roles carry the NightWatchBoundary permissions boundary like every other role.

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
    deploy = "repo:${var.github_repo}:environment:${var.deploy_environment}"
    plan   = "repo:${var.github_repo}:pull_request"
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

data "aws_iam_policy_document" "plan" {
  # Reads only. The boundary already narrows these to NightWatch's own resources.
  statement {
    sid = "ReadEverythingTerraformManages"
    actions = [
      "budgets:ViewBudget",
      "budgets:ListTagsForResource",
      "cloudwatch:Describe*",
      "cloudwatch:List*",
      "dynamodb:Describe*",
      "dynamodb:GetItem", # seeded ShopLite products are Terraform resources too
      "dynamodb:List*",
      "events:Describe*",
      "events:List*",
      "iam:Get*",
      "iam:List*",
      "lambda:Get*",
      "lambda:List*",
      "logs:Describe*",
      "logs:List*",
      "scheduler:Get*",
      "scheduler:List*",
      "ssm:Describe*",
      "ssm:Get*",
      "ssm:List*",
      "states:Describe*",
      "states:List*",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "ReadState"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [var.state_bucket_arn, "${var.state_bucket_arn}/*"]
  }
}
