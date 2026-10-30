variable "package_path" {
  description = "NightWatch code zip from `make build` (the API runs from it)."
  type        = string
}

variable "layer_arn" {
  description = "The nightwatch-deps layer (third-party packages, including PyJWT)."
  type        = string
}

variable "boundary_policy_arn" {
  description = "ARN of the NightWatchBoundary policy. Every role created here must carry it (ADR 0006)."
  type        = string
}

variable "table_name" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "tenant_id" {
  type    = string
  default = "default"
}

variable "postmortem_bucket" {
  type = string
}

variable "postmortem_bucket_arn" {
  type = string
}

variable "kill_switch_param" {
  type    = string
  default = "/nightwatch/kill_switch"
}

variable "llm_providers" {
  description = "Shown on the Settings page."
  type        = string
}

variable "users" {
  description = "Email addresses that may sign in. Cognito emails each a temporary password."
  type        = list(string)
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "tags" {
  type    = map(string)
  default = {}
}
