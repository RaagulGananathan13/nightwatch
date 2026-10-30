output "url" {
  description = "The dashboard."
  value       = local.site_url
}

output "bucket" {
  description = "Where CI uploads the built app."
  value       = aws_s3_bucket.site.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.site.arn
}

output "distribution_id" {
  description = "CI invalidates its cache after each upload."
  value       = aws_cloudfront_distribution.dashboard.id
}

output "distribution_arn" {
  value = aws_cloudfront_distribution.dashboard.arn
}

output "user_pool_id" {
  value = aws_cognito_user_pool.dashboard.id
}
