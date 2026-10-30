# 0016. Fix-as-pull-request: Terraform changes for settings that are too small

- Status: accepted
- Date: 2026-10-30
- Spec sections: F10 (Phase 8)

## Context

Runbooks undo a change, such as a bad deploy, a flag flip or a capacity cut. Some incidents have
no change to undo: traffic simply outgrew the table's provisioned capacity, or the function
genuinely needs more memory. Raising the value by hand in the console would fix it today, but
the next `make apply` would quietly put it back. The lasting fix is a Terraform change, and
that should be reviewed like any other code.

## Decision

1. **Detect from evidence, never from the model's text.** `config_fix.detect` reads the
   structured evidence, and an earlier change rules a pull request out:

   | Setting | Evidence that it is too small | Ruled out when |
   |---|---|---|
   | Read capacity | Throttling | A capacity change is in the window |
   | Memory | Out-of-memory errors at 95% or more of the function's memory | A flag is on or a deploy is recent |
   | Timeout | Timeouts at 95% or more of the limit | A flag is on or a deploy is recent |

   An earlier change means a runbook can undo it.
2. **A catalogue says what may change.** `services.yaml` lists each Terraform setting a pull
   request may raise:
   - the file;
   - the top-level block;
   - the attribute;
   - a ceiling, such as 10 read units, which keeps the account inside the 25-unit free tier.

   The edit changes that one number and nothing else in the file, and doubles it up to the
   ceiling. A setting not in the catalogue can't be touched.
3. **A pull request, not an apply.**
   - **Open:** the `nightwatch-fixpr` Lambda commits the change on its own branch and opens a
     pull request. The description has the diagnosis and the cited evidence, with a link to
     the incident in the dashboard.
   - **Review:** CI runs `terraform plan` on the pull request, including the free-tier guard.
   - **Apply:** a person merges, then runs `make apply`. Applies stay manual (ADR 0008).
4. **A token narrowed to one repository.** The GitHub token is fine-grained: this one
   repository, with only Contents and Pull requests write access. You create it and keep it in
   SSM. The Lambda's AWS role can read that token, the webhook and the kill switch, and write
   to NightWatch's table: nothing else in AWS.
5. **Untrusted text stays inert.** Diagnosis and evidence text can come from logs. In the pull
   request it can't `@mention` anyone (which would notify them) or add HTML.
6. **Kill switch and idempotency.** The kill switch stops pull requests too, so evaluation runs
   never open any. A second run for the same incident finds the open pull request instead of
   making another.

## Consequences

- An incident caused by growth ends with a reviewed, permanent change, not console drift.
- The demo scenario is `traffic-surge`: nothing changes, and steady reads outgrow 5 read units.
  The right fix is this pull request, and the runbooks correctly propose nothing.
- Only three settings are covered: read capacity, memory and timeout. A new one needs an entry
  in the catalogue and its detection rule.
