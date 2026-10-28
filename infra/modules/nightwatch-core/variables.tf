variable "package_path" {
  description = "NightWatch code zip from `make build` (deployed by Terraform)."
  type        = string
}

variable "layer_path" {
  description = "Third-party packages layer zip from `make build`."
  type        = string
}

variable "boundary_policy_arn" {
  description = "ARN of the NightWatchBoundary policy. Every role created here must carry it (ADR 0006)."
  type        = string
}

variable "tenant_id" {
  description = "Tenant prefix for every DynamoDB key. Single-tenant for now (ADR 0009)."
  type        = string
  default     = "default"
}

variable "discord_webhook_param" {
  description = "SSM SecureString holding the Discord webhook URL. You create it yourself; Terraform never sees the value."
  type        = string
  default     = "/nightwatch/discord/webhook_url"
}

variable "flap_window_minutes" {
  description = "An alarm that fires again this soon after its incident resolved reopens it (ADR 0010)."
  type        = number
  default     = 10
}

variable "read_capacity" {
  description = "Provisioned read units for the nightwatch table (counts toward the 25-unit free tier)."
  type        = number
  default     = 8
}

variable "write_capacity" {
  description = "Provisioned write units for the nightwatch table (counts toward the 25-unit free tier)."
  type        = number
  default     = 8
}

variable "index_capacity" {
  description = "Read and write units for GSI1 (each counts toward the free tier)."
  type        = number
  default     = 2
}

variable "log_retention_days" {
  description = "How long CloudWatch keeps NightWatch logs."
  type        = number
  default     = 14
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
