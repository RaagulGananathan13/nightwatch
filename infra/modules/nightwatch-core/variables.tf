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

variable "watched" {
  description = "Resources of the services NightWatch investigates. The investigator may only read these (spec section 11)."
  type = object({
    function_arns  = list(string)
    log_group_arns = list(string)
    table_arns     = list(string)
    parameter_arns = list(string)
  })
}

variable "llm_providers" {
  description = "LLMs to try in order, as provider:model (ADR 0003). Providers without a key in SSM are skipped."
  type        = string
  default     = "groq:openai/gpt-oss-120b,groq:qwen/qwen3.8-27b"
}

variable "llm_daily_requests" {
  description = "Requests per LLM model per day, kept well under the free tier (Groq allows 1,000)."
  type        = number
  default     = 300
}

variable "settle_seconds" {
  description = "How long the workflow waits for related alarms before investigating."
  type        = number
  default     = 30
}

variable "discord_bot_token_param" {
  description = "SSM SecureString holding the Discord bot token, used to post approval cards. You create it yourself."
  type        = string
  default     = "/nightwatch/discord/bot_token"
}

variable "discord_channel_id" {
  description = "Discord channel for approval cards (the incidents channel). Empty turns approval requests off."
  type        = string
  default     = ""
}

variable "discord_public_key" {
  description = "The Discord application's public key, used to verify every button click (it is public by design)."
  type        = string
  default     = ""
}

variable "approval_timeout_minutes" {
  description = "How long a fix waits for approval before NightWatch escalates and gives up on it."
  type        = number
  default     = 30
}

variable "github_token_param" {
  description = "SSM SecureString with a fine-grained GitHub token for fix pull requests (F10). You create it."
  type        = string
  default     = "/nightwatch/github/token"
}

variable "kill_switch_param" {
  description = "SSM parameter that turns every fix off unless its value is false (spec F14)."
  type        = string
  default     = "/nightwatch/kill_switch"
}

variable "mcp_users" {
  description = "IAM user or role ARNs that may run the MCP server (switch to nightwatch-mcp)"
  type        = list(string)
}
