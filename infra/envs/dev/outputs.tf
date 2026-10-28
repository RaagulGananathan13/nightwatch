output "shoplite_url" {
  description = "ShopLite's endpoint (requests must be SigV4-signed). Save as repo variable SHOPLITE_URL."
  value       = module.shoplite.function_url
}

output "shoplite_function" {
  value = module.shoplite.function_name
}

output "shoplite_table" {
  value = module.shoplite.table_name
}

output "alarm_names" {
  value = module.alarms.alarm_names
}

output "github_deploy_role_arn" {
  description = "Save as repo variable AWS_DEPLOY_ROLE_ARN."
  value       = module.iam.deploy_role_arn
}

output "github_plan_role_arn" {
  description = "Save as repo variable AWS_PLAN_ROLE_ARN."
  value       = module.iam.plan_role_arn
}

output "free_tier_usage" {
  description = "How much of the shared always-free allowances this environment uses."
  value = {
    dynamodb_read_units  = "${local.dynamodb_read_units} of 25"
    dynamodb_write_units = "${local.dynamodb_write_units} of 25"
    alarm_metrics        = "${local.alarm_metrics} of 10"
  }
}
