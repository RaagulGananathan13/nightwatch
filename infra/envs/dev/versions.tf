terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.70"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.8"
    }
  }

  # The bucket name comes from backend.hcl (git-ignored) locally, or -backend-config in CI.
  # S3-native locking replaces the old DynamoDB lock table (ADR 0004).
  backend "s3" {
    key          = "envs/dev/terraform.tfstate"
    region       = "us-east-2"
    encrypt      = true
    use_lockfile = true
  }
}
