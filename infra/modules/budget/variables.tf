variable "name" {
  description = "Budget name. Must start with nightwatch- (the deployer may only manage those)."
  type        = string
  default     = "nightwatch-monthly-cost"
}

variable "limit_usd" {
  description = "Monthly limit in US dollars."
  type        = number
  default     = 1
}

variable "alert_email" {
  description = "Where the alerts go."
  type        = string
  sensitive   = true
}

variable "services" {
  description = "Only count these AWS services (Cost Explorer names). Empty means the whole account."
  type        = list(string)
  default     = []
}
