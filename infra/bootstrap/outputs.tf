output "state_bucket" {
  description = "Name of the S3 bucket that holds Terraform state."
  value       = aws_s3_bucket.state.bucket
}

output "backend_config" {
  description = "Paste into infra/envs/dev/backend.hcl (git-ignored) before running terraform init there."
  value       = "bucket = \"${aws_s3_bucket.state.bucket}\""
}
