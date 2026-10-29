# Chaos lab

Scripts that inject known failures into ShopLite and check how NightWatch responds. Each
scenario records its ground-truth root cause, which the evaluation harness (Phase 5) scores
NightWatch's diagnoses against.

Every scenario undoes itself afterwards, even if you press Ctrl+C. Run them from the repo root
with the deployer profile.

| Scenario | Command | What breaks | The diagnosis must name |
|---|---|---|---|
| Bad deploy | `make chaos-bad-deploy` | A real commit (made in a throwaway git worktree, never pushed) renames the price field ShopLite reads. It ships as a new Lambda version and listings and orders raise `KeyError`. | the commit SHA |
| Error flag | `make chaos-error-flag` | ShopLite's feature flag fails 80% of requests | the `/shoplite/flags` change |

While the fault is on, each script sends a request every 2 seconds and prints NightWatch's
progress: incident opened, alerts grouped, investigation done. It then waits for the alarms to
recover and gives a verdict. It passes only if:
- exactly one incident opened, with exactly one Discord message;
- an investigation finished, and its top hypothesis is verified by the evidence guard;
- the diagnosis names what really broke.

That is the acceptance test for Phases 2 and 3. More scenarios (throttling, timeouts, memory, a
broken environment variable, a slow dependency) arrive in Phase 5.

## Let NightWatch fix it (Phase 4)

| Command | What happens |
|---|---|
| `make chaos-fix` | The bad deploy stays live until NightWatch's rollback is approved and verified |
| `make chaos-fix-flag` | The error flag stays on until NightWatch's flag reset is approved and verified |

The fault and the traffic keep going while you decide. When the approval card appears in
Discord, click **Approve**. The run passes if:
- the fix ran and verification passed;
- the audit log shows `remediation_proposed`, `approval_requested`, `approval_decided`,
  `remediation_executed` and `remediation_verified`, in that order.

Click **Reject** instead and the run passes only if nothing changed: the fault was still in
place until the script removed it. If nobody decides within 20 minutes the script gives up. As
always, it removes the fault at the end.
