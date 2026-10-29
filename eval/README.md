# Evaluation harness

`make eval` runs every chaos scenario against the deployed stack and scores NightWatch
(spec F16). It takes about 2.5 hours, because each scenario waits out the previous incident's
10-minute flap window. Leave it running; Ctrl+C stops it cleanly.

```bash
make eval                                           # all scenarios
python -m eval.run --only lambda-timeout bad-deploy # re-run some, keep the rest
```

For each scenario it:
1. injects the fault;
2. sends traffic;
3. undoes the fault and waits for the alarms to recover;
4. scores the incident that NightWatch opened.

The kill switch is on for the whole run, so fixes are proposed and recorded but never run, and
nobody is pinged to approve them. It is set back to its previous value at the end.

It writes two files after every scenario, so a run that stops halfway keeps what it measured:
- [results.json](results.json): the raw measurements per scenario;
- [results.md](results.md): the tables, every top hypothesis word for word, and the phrases
  each scenario is scored on.

| Metric | Meaning |
|---|---|
| Top-1 / top-3 accuracy | The top hypothesis (or any of the top 3) names the real cause. Naming only the symptom doesn't count. |
| Time to diagnosis | From the alarm to the diagnosis in Discord |
| Fault to diagnosis | From injecting the fault to the diagnosis in Discord |
| Evidence validity | Hypotheses whose cited numbers and commits the guard found in the evidence |
| Right fix proposed | NightWatch proposed the runbook that fixes the scenario |
| LLM use | Calls and tokens per incident |

How the scoring works, and its limits, is in
[ADR 0013](../docs/decisions/0013-evaluating-nightwatch.md).
