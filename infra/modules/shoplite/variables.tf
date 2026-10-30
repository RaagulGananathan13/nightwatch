variable "package_path" {
  description = "Path to the ShopLite zip built by `make build`. Only used when the function is first created."
  type        = string
}

variable "boundary_policy_arn" {
  description = "ARN of the NightWatchBoundary policy. Every role created here must carry it (ADR 0006)."
  type        = string
}

variable "read_capacity" {
  description = "Provisioned read capacity for the shoplite table (counts toward the 25-unit free tier)."
  type        = number
  default     = 10
}

variable "write_capacity" {
  description = "Provisioned write capacity for the shoplite table (counts toward the 25-unit free tier)."
  type        = number
  default     = 5
}

variable "log_retention_days" {
  description = "How long CloudWatch keeps ShopLite logs."
  type        = number
  default     = 7
}

variable "canary_enabled" {
  description = "Send steady test traffic to ShopLite. Turn off to pause all background traffic."
  type        = bool
  default     = true
}

variable "canary_schedule" {
  description = "How often the canary runs (EventBridge Scheduler expression)."
  type        = string
  default     = "rate(1 minute)"
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
