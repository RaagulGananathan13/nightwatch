# 0004. Terraform state in S3 with native locking

- Status: accepted
- Date: 2026-10-28
- Spec sections: 6, 14 (Phase 1)

## Context

The spec stores remote state in S3 and locks it with a DynamoDB table. Terraform 1.10 added
S3-native locking (`use_lockfile`), and Terraform 1.11 made it generally available and
deprecated DynamoDB-based locking. A lock table would also use part of the shared
25 RCU and 25 WCU free allowance ([0002](0002-dynamodb-provisioned-capacity.md)).

## Decision

`infra/bootstrap` creates one S3 bucket for state with these settings:

- versioning on
- SSE-S3 encryption
- all public access blocked
- TLS-only bucket policy
- old versions expire after 30 days

Every environment's backend sets `use_lockfile = true`, and Terraform 1.11 or later is required.
The dev machine and CI both run 1.16.

## Consequences

- One fewer resource, and no DynamoDB capacity spent on locking.
- S3 only has a 12-month free tier. State files are a few KB, so the cost afterwards is a small
  fraction of a cent per month.
