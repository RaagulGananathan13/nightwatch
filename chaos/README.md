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
