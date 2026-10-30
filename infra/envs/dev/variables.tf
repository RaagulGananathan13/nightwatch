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

variable "github_subject_prefix" {
  description = "OIDC subject prefix GitHub uses for this repo (immutable IDs). See modules/iam."
  type        = string
  default     = "repo:RaagulGananathan13@181767902/nightwatch@1436929923"
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

variable "dashboard_users" {
  description = "Email addresses that may sign in to the dashboard. Empty means just budget_alert_email."
  type        = list(string)
  default     = []
}

variable "discord_channel_id" {
  description = "Discord channel where approval cards are posted (#incidents)."
  type        = string
  default     = "1565026740634390659"
}

variable "discord_public_key" {
  description = "Public key of the NightWatch Discord application (Developer Portal, General Information)."
  type        = string
  default     = "97504cad6de4bc986b0886ca787896e87e5aacf8e015d99ce863040e29d086d1"
}
