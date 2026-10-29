output "table_name" {
  value = aws_dynamodb_table.this.name
}

output "ingest_function_name" {
  value = aws_lambda_function.ingest.function_name
}

output "dead_letter_queue" {
  description = "Alerts that failed every retry. Should stay empty."
  value       = aws_sqs_queue.ingest_dlq.name
}

output "provisioned_capacity" {
  description = "Read and write units this module uses (table plus index), for the free-tier guard."
  value = {
    read  = aws_dynamodb_table.this.read_capacity + var.index_capacity
    write = aws_dynamodb_table.this.write_capacity + var.index_capacity
  }
}

output "state_machine_arn" {
  description = "The incident workflow. Open it in the Step Functions console to see each run."
  value       = aws_sfn_state_machine.incident.arn
}

output "investigate_function_name" {
  value = aws_lambda_function.investigate.function_name
}

output "interactions_url" {
  description = "Discord's Interactions Endpoint URL for the NightWatch application."
  value       = aws_lambda_function_url.interactions.function_url
}

output "kill_switch_parameter" {
  value = aws_ssm_parameter.kill_switch.name
}
