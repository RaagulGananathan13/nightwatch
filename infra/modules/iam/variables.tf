variable "github_subject_prefix" {
  description = "The repository's OIDC subject prefix: gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix"
  type        = string

  validation {
    condition     = can(regex("^repo:[A-Za-z0-9-]+@[0-9]+/[A-Za-z0-9._-]+@[0-9]+$", var.github_subject_prefix))
    error_message = "Use the immutable form repo:owner@<owner id>/name@<repo id>."
  }
}

variable "deploy_environment" {
  description = "GitHub environment whose jobs may deploy. Restrict it to the main branch in repo settings."
  type        = string
  default     = "dev"
}

variable "create_oidc_provider" {
  description = "False if the account already has a GitHub OIDC provider (only one is allowed)."
  type        = bool
  default     = true
}

variable "boundary_policy_arn" {
  description = "ARN of the NightWatchBoundary policy."
  type        = string
}

variable "deployable_function_arns" {
  description = "Lambda functions (unqualified ARNs) that CI may ship new code to."
  type        = list(string)
}

variable "state_bucket_arn" {
  description = "Terraform state bucket, read by the plan role."
  type        = string
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
