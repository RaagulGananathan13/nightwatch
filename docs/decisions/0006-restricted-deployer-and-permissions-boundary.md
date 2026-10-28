# 0006. Restricted deployer user and a permissions boundary

- Status: accepted
- Date: 2026-10-28
- Spec sections: 4 (least privilege), 11

## Context

The AWS account already hosts another project in the same Region (us-east-2). Admin access
keys on a laptop are risky. Terraform still needs to create IAM roles for the Lambdas, the
Step Functions workflow and GitHub Actions. If a deployer can create roles with any policy,
it is effectively an administrator.

## Decision

- A CloudShell script, run once by the account owner, creates the IAM user
  **`nightwatch-deployer`** and the local CLI profile **`nightwatch`**. The user's policy only
  covers resources whose names start with `nightwatch` or `shoplite`, in **us-east-2**, plus
  the global services NightWatch needs (IAM for its own roles, Budgets, CloudFront).
- Every IAM role Terraform creates must sit under the `/nightwatch/` path and carry the
  **`NightWatchBoundary`** permissions boundary. The deployer is denied editing the boundary or
  removing it from a role, so no role can ever exceed it.
- GitHub Actions authenticates with OIDC and short-lived tokens, so no access keys are stored
  in GitHub.

## Consequences

- The deployer can't touch other projects in the account or escalate itself to admin.
- When a new AWS action is needed, the account owner reruns the script to update the policy.
