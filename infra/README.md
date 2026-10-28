# Infrastructure (Terraform)

Filled in Phase 1. Everything NightWatch runs on is created by `terraform apply` and removed by
`terraform destroy`.

| Folder | Purpose |
|---|---|
| `bootstrap/` | One-time setup: the S3 bucket that holds Terraform state (native S3 locking, no lock table) |
| `modules/` | Reusable pieces: `budget`, `iam`, `shoplite`, `alarms`, `nightwatch-core`, `dashboard` |
| `envs/dev/` | Wires the modules together for the dev environment (region us-east-2) |
