# Chaos lab

Scripts that inject known failures into ShopLite and check how NightWatch responds. Each
scenario records its ground-truth root cause, which the evaluation harness (Phase 5) scores
NightWatch's diagnoses against.

Every scenario restores ShopLite afterwards, even if you press Ctrl+C. Run them from the repo
root with the deployer profile.

| Scenario | Command | What breaks | Expected alarms |
|---|---|---|---|
| Bad deploy | `make chaos-bad-deploy` | ShopLite's error flag fails 80% of requests for 4 minutes | `shoplite-5xx-rate`, `shoplite-lambda-errors` |

`bad_deploy` sends a request every 2 seconds while the fault is on, watches for NightWatch's
incident, then waits for the alarms to recover. It exits with code 0 only if exactly one incident
opened and exactly one Discord message was posted, which is Phase 2's acceptance test.

In Phase 2 the bad deploy is simulated with the error flag, as the spec asks. Phase 3 replaces it
with a real broken build shipped as a new Lambda version, so the diagnosis can name the commit.
More scenarios (throttling, timeouts, memory, a broken environment variable, a slow dependency)
arrive in Phase 5.
