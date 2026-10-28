# 0008. CI ships code, reviewed Terraform ships infrastructure

- Status: accepted
- Date: 2026-10-28
- Spec sections: 8, 14 (Phase 1), 19

## Context

The spec wants GitHub Actions to deploy ShopLite on every merge to main, so that "recent
deploy" evidence is real. It also says `terraform apply` never runs without a human approving a
plan. If CI applied Terraform on every merge, it would need near-admin rights in a public
repository, and an infrastructure change could go live without anyone reading its plan.

## Decision

- **Infrastructure** changes through Terraform. A person runs `make plan`, reads the saved
  plan, then runs `make apply`. Pull requests get a read-only `terraform plan` in CI through the
  `github-plan` role.
- **Code** changes through CI. On merge to main, `deploy.yml` builds a reproducible zip stamped
  with the commit SHA and uploads it. It publishes an immutable Lambda version described as
  `sha=<commit> run=<id>`, moves the `live` alias, then smoke-tests the endpoint. The
  `github-deploy` role can only do those Lambda calls. Only jobs in the `dev` GitHub
  environment can assume it, and that environment only deploys from main.
- Terraform creates the function, its first version and the alias, then ignores later code and
  alias changes (`ignore_changes`). This stops Terraform undoing a deploy or a rollback.

## Consequences

- Every deploy is a real, timestamped event that NightWatch can read without waiting for
  CloudTrail. The version description names the commit, and the alias shows what is live.
- Rolling back is just moving the alias to an earlier version, which is the runbook in Phase 4.
- Configuration changes made by Terraform (memory, environment variables) publish a new
  version, but it only goes live on the next deploy. This is documented in the ShopLite README.
- **NightWatch's own Lambdas** (from Phase 2) are deployed by Terraform, which tracks each zip's
  hash. The control plane changes less often than the demo app, its code and IAM usually
  change together, and every change still goes through a reviewed `make plan` and `make apply`.
