output "function_name" {
  value = aws_lambda_function.api.function_name
}

output "function_arn" {
  value = aws_lambda_function.api.arn
}

output "alias_arn" {
  value = aws_lambda_alias.live.arn
}

output "function_url" {
  description = "ShopLite's HTTPS endpoint. Requests must be SigV4-signed (AWS_IAM auth)."
  value       = aws_lambda_function_url.api.function_url
}

output "table_name" {
  value = aws_dynamodb_table.this.name
}

output "table_arn" {
  value = aws_dynamodb_table.this.arn
}

output "flags_parameter" {
  value = aws_ssm_parameter.flags.name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.api.name
}

output "provisioned_capacity" {
  description = "Read and write units this module uses, for the free-tier guard."
  value = {
    read  = aws_dynamodb_table.this.read_capacity
    write = aws_dynamodb_table.this.write_capacity
  }
}

output "log_group_arn" {
  value = aws_cloudwatch_log_group.api.arn
}

output "flags_parameter_arn" {
  value = aws_ssm_parameter.flags.arn
}
