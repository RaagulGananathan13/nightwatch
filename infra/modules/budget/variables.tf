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

variable "sns_topic_arn" {
  description = "SNS topic that also receives the alerts (NightWatch's cost guardian). Empty: email only."
  type        = string
  default     = ""
}

variable "stop_at_usd" {
  description = "Also alert at this real spend, the emergency stop's line. 0: no such alert."
  type        = number
  default     = 0
}
