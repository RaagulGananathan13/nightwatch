# Chaos lab

Scripts that inject known failures into ShopLite and check how NightWatch responds. Each
scenario records its ground-truth root cause, which the evaluation harness (Phase 5) scores
NightWatch's diagnoses against.

Every scenario undoes itself afterwards, even if you press Ctrl+C. Run them from the repo root
with the deployer profile.

Run any scenario with `make chaos NAME=<scenario>`, and list them with `make chaos-list`.
The definitions live in [scenarios.py](scenarios.py).

| Scenario | What breaks | The diagnosis must name |
|---|---|---|
| `error-flag` | ShopLite's feature flag fails 80% of requests | the `/shoplite/flags` change |
| `bad-deploy` | A real commit renames the price field ShopLite reads. It is made in a throwaway git worktree and never pushed. It ships as a new Lambda version, and listings and orders raise `KeyError`. | the commit SHA |
| `dependency-latency` | The `extra_latency_ms` flag adds 1.5 s to every request, like a slow dependency | the flag, as the cause of the latency |
| `lambda-timeout` | The `hang` flag makes every request run past the 5-second timeout | the flag, not just "timeouts" |
| `memory-exhaustion` | The `memory_hog_mb` flag allocates 400 MB per request in a 256 MB function | the flag, as the cause of running out of memory |
| `dynamodb-throttle` | The table's read capacity drops from 5 units to 1 under steady load | the capacity change, not just "throttling" |
| `broken-env-var` | A new version with the same code but `TABLE_NAME` pointing at a missing table | the environment variable or the missing table |

While the fault is on, each scenario sends traffic: one request every 2 seconds, or about 20 a
second for throttling. It prints NightWatch's progress as it goes: incident opened, alerts
grouped, investigation done. Then it waits for the alarms to recover and gives a verdict. It
passes only if:
- exactly one incident opened, with exactly one Discord message;
- an investigation finished, and its top hypothesis is verified by the evidence guard;
- the diagnosis names the cause, not just the symptom.

A scenario waits before it starts if an earlier incident is still open or resolved less than 10
minutes ago. Otherwise its alarms would join or reopen that incident instead of opening a new
one. `make eval` runs all of them and scores the results ([eval/](../eval/README.md)).

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
