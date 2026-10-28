variable "region" {
  description = "AWS region for everything in this environment."
  type        = string
  default     = "us-east-2"
}

variable "github_repo" {
  description = "owner/name of the GitHub repo whose Actions may deploy."
  type        = string
  default     = "RaagulGananathan13/nightwatch"
}

variable "create_github_oidc_provider" {
  description = "Set false if the CloudShell script said a GitHub OIDC provider already exists."
  type        = bool
  default     = true
}

variable "budget_alert_email" {
  description = "Email address for AWS Budget alerts."
  type        = string
  sensitive   = true
}

variable "budget_limit_usd" {
  description = "Monthly budget limit in US dollars."
  type        = number
  default     = 1
}

variable "budget_services" {
  description = "Services the budget counts (Cost Explorer names). Empty means the whole account."
  type        = list(string)
  # NightWatch's own services, so other projects in the same account don't trigger alerts.
  default = [
    "AWS Lambda",
    "Amazon DynamoDB",
    "AWS Step Functions",
    "AmazonCloudWatch",
    "Amazon Simple Storage Service",
  ]
}

variable "canary_enabled" {
  description = "Send steady test traffic to ShopLite."
  type        = bool
  default     = true
}

variable "canary_schedule" {
  description = "How often the ShopLite canary runs."
  type        = string
  default     = "rate(1 minute)"
}
