variable "service" {
  description = "Service name. Alarm names start with it, and NightWatch groups alerts by it."
  type        = string
}

variable "function_name" {
  description = "Lambda function behind the service's function URL."
  type        = string
}

variable "table_name" {
  description = "DynamoDB table the service uses."
  type        = string
}

variable "error_rate_percent" {
  description = "Alarm when more than this share of requests return 5xx in a minute."
  type        = number
  default     = 5
}

variable "latency_p95_ms" {
  description = "Alarm when p95 latency stays above this for two minutes."
  type        = number
  default     = 1000
}

variable "lambda_error_threshold" {
  description = "Alarm when the function fails this many times in a minute."
  type        = number
  default     = 2
}

variable "tags" {
  description = "Extra tags for every alarm."
  type        = map(string)
  default     = {}
}
