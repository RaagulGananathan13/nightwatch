# The dev environment: everything NightWatch needs in one AWS account and region.
# Run it through make (see `make help`), which sets AWS_PROFILE=nightwatch locally.

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project      = "nightwatch"
      environment  = "dev"
      "managed-by" = "terraform"
      repo         = var.github_repo
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id          = data.aws_caller_identity.current.account_id
  boundary_policy_arn = "arn:aws:iam::${local.account_id}:policy/nightwatch/NightWatchBoundary"
  state_bucket_arn    = "arn:aws:s3:::nightwatch-${local.account_id}-tfstate"
}

module "budget" {
  source      = "../../modules/budget"
  limit_usd   = var.budget_limit_usd
  alert_email = var.budget_alert_email
  services    = var.budget_services
}

module "shoplite" {
  source              = "../../modules/shoplite"
  package_path        = "${path.root}/../../../.build/shoplite-api.zip"
  boundary_policy_arn = local.boundary_policy_arn
  canary_enabled      = var.canary_enabled
  canary_schedule     = var.canary_schedule
}

module "nightwatch_core" {
  source              = "../../modules/nightwatch-core"
  package_path        = "${path.root}/../../../.build/nightwatch.zip"
  layer_path          = "${path.root}/../../../.build/nightwatch-deps.zip"
  boundary_policy_arn = local.boundary_policy_arn
}

module "alarms" {
  source        = "../../modules/alarms"
  service       = "shoplite"
  function_name = module.shoplite.function_name
  table_name    = module.shoplite.table_name
}

module "iam" {
  source                   = "../../modules/iam"
  github_subject_prefix    = var.github_subject_prefix
  create_oidc_provider     = var.create_github_oidc_provider
  boundary_policy_arn      = local.boundary_policy_arn
  deployable_function_arns = [module.shoplite.function_arn]
  state_bucket_arn         = local.state_bucket_arn
}

# ------------------------------------------------------------------- free-tier guard
# Stops `terraform plan` before anything would cost money. Other projects in the same account
# share these allowances, so leave headroom (ADR 0002, ADR 0007).

locals {
  dynamodb_read_units  = module.shoplite.provisioned_capacity.read + module.nightwatch_core.provisioned_capacity.read
  dynamodb_write_units = module.shoplite.provisioned_capacity.write + module.nightwatch_core.provisioned_capacity.write
  alarm_metrics        = module.alarms.alarm_metric_count
}

resource "terraform_data" "free_tier_guard" {
  input = {
    read_units    = local.dynamodb_read_units
    write_units   = local.dynamodb_write_units
    alarm_metrics = local.alarm_metrics
  }

  lifecycle {
    precondition {
      condition     = local.dynamodb_read_units <= 25 && local.dynamodb_write_units <= 25
      error_message = "DynamoDB provisioned capacity is over the 25 RCU / 25 WCU always-free tier."
    }

    precondition {
      condition     = local.alarm_metrics <= 10
      error_message = "CloudWatch alarms would use more than the 10 free alarm metrics."
    }
  }
}
