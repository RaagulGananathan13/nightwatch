# Chaos testing

The chaos lab breaks the ShopLite demo application in known ways, then checks NightWatch's
response. Use it to see NightWatch work, and to check that a change to NightWatch didn't make it
worse.

!!! warning "Use your own dev environment only"
    Every scenario changes real resources in your AWS account: flags, deployed versions or table
    capacity. Each one undoes itself afterwards, even if you press Ctrl+C. The traffic it sends
    stays inside the free tier.

## Run a scenario

```bash
make chaos-list                 # every scenario and what its diagnosis must name
make chaos NAME=error-flag      # run one
```

| Scenario | What breaks | The diagnosis must name |
|---|---|---|
| `error-flag` | A feature flag fails 80% of requests | The flags parameter change |
| `bad-deploy` | A real commit renames a field ShopLite reads and ships as a new version. It is made in a throwaway git worktree and never pushed. | The commit |
| `dependency-latency` | A flag adds 1.5 seconds to every request, like a slow dependency | The flag, as the cause of the latency |
| `lambda-timeout` | A flag makes every request run past the 5-second timeout | The flag, not just "timeouts" |
| `memory-exhaustion` | A flag allocates 400 MB per request in a 256 MB function | The flag, as the cause of running out of memory |
| `dynamodb-throttle` | The table's read capacity drops from 5 units to 1 under steady load | The capacity change, not just "throttling" |
| `broken-env-var` | A new version points `TABLE_NAME` at a table that doesn't exist | The environment variable or the missing table |

While the fault is on, the scenario sends traffic and prints NightWatch's progress: incident
opened, alerts grouped, investigation done. It passes only if:

- exactly one incident opened, with one chat message;
- an investigation finished, and the guard verified its top hypothesis;
- the diagnosis names the cause, not just the symptom.

A scenario waits before it starts if an earlier incident is still open or resolved less than
10 minutes ago. Otherwise its alarms would join that incident instead of opening a new one.

## Let NightWatch fix it

These two keep the fault in place until you approve NightWatch's fix in chat. They need
[approval buttons](chat.md).

| Command | What happens |
|---|---|
| `make chaos-fix` | A bad deploy stays live until you approve the rollback and it is verified |
| `make chaos-fix-flag` | The error flag stays on until you approve the flag reset and it is verified |

## Run the evaluation

```bash
make eval    # about 2.5 hours
```

`make eval` runs every scenario, scores each diagnosis against the scenario's ground truth, and
writes `eval/results.md`. It measures root-cause accuracy, time to diagnosis, how many cited
facts the guard verified, and whether the right fix was proposed. The
[published results](https://github.com/RaagulGananathan13/nightwatch/blob/main/eval/results.md)
are from the reference deployment.

The kill switch stays on during the evaluation, so no fix runs. If you stop `make eval` part
way through, turn the kill switch back off
([how](operations.md#the-kill-switch)).
