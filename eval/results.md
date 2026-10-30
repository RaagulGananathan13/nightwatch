# NightWatch evaluation results

Measured on 2026-10-29 against the live dev stack by `make eval`: 7 chaos scenarios, each a real fault injected into ShopLite (commit efc0fd6d7f19, prompt investigate_v1, model openai/gpt-oss-120b). Every number below comes from those runs.

| Metric | Result |
|---|---|
| Top-1 root-cause accuracy | **7/7 (100%)** |
| Top-3 root-cause accuracy | 7/7 (100%) |
| Mean time to diagnosis (alarm → diagnosis in Discord) | **46 s** |
| Mean time from fault to diagnosis | 2 min 01 s |
| Evidence validity (hypotheses the guard verified) | **11/11 (100%)** |
| … verified as the model wrote them, before citation repair | 11/11 (100%) |
| Faults detected (an incident opened) | 7/7 (100%) |
| Right fix proposed | 7/7 (100%) |
| Alerts grouped into incidents | 17 alerts → 7 incidents |
| LLM use per incident | 1.0 calls, 3,468 tokens |

## Scenarios

| Scenario | Incident | Fault → alarm | Alarm → diagnosis | Top-1 | Top-3 | Verified | LLM | Fix proposed |
|---|---|---|---|---|---|---|---|---|
| error flag (error_rate=0.8) | INC-008 (2 alerts) | 44 s | 46 s | ✓ | ✓ | 1/1 | 1 calls, 3,200 tokens | reset-feature-flags ✓ |
| bad deploy | INC-009 (2 alerts) | 62 s | 45 s | ✓ | ✓ | 1/1 | 1 calls, 3,538 tokens | rollback-lambda-alias ✓ |
| slow dependency | INC-010 (1 alerts) | 2 min 16 s | 45 s | ✓ | ✓ | 2/2 | 1 calls, 3,141 tokens | reset-feature-flags ✓ |
| Lambda timeout | INC-011 (3 alerts) | 76 s | 46 s | ✓ | ✓ | 1/1 | 1 calls, 3,816 tokens | reset-feature-flags ✓ |
| memory exhaustion | INC-012 (3 alerts) | 17 s | 45 s | ✓ | ✓ | 3/3 | 1 calls, 3,303 tokens | reset-feature-flags ✓ |
| DynamoDB throttling | INC-013 (4 alerts) | 1 min 56 s | 46 s | ✓ | ✓ | 2/2 | 1 calls, 3,666 tokens | raise-dynamodb-capacity ✓ |
| broken environment variable | INC-014 (2 alerts) | 76 s | 45 s | ✓ | ✓ | 1/1 | 1 calls, 3,612 tokens | rollback-lambda-alias ✓ |

## What NightWatch said

**error flag (error_rate=0.8)**. Ground truth: ShopLite's error flag (/shoplite/flags) was switched on, so requests fail with InjectedFaultError.

> A recent SSM parameter change set /shoplite/flags error_rate to 0.8, and the new Lambda code (version 12) respects this flag and deliberately injects faults, leading to >=2 errors per minute and triggering the alarm. (93% confidence)

**bad deploy**. Ground truth: Commit 'read product prices from the new price field' was deployed and makes product listings and orders raise KeyError: 'price'.

> Version 13 of shoplite‑api (sha=28af2eda514c) introduced a KeyError on missing 'price' field, leading to 30 errors and 5xx rate >5% (95% confidence)

**slow dependency**. Ground truth: The extra_latency_ms flag adds 1.5 s to every request, like a slow dependency, so p95 latency stays above the 1-second alarm.

> Setting /shoplite/flags to {"extra_latency_ms":1500} introduced artificial delay in the Lambda, causing p95 latency to exceed 1000 ms. (92% confidence)

**Lambda timeout**. Ground truth: The hang flag in /shoplite/flags makes every request run past ShopLite's 5-second timeout, so Lambda stops it ('Task timed out').

> The recent SSM flag change to {"hang": true} (E5) caused the live Lambda version 12 (sha=efc0fd6d7f19, E14) to hang, leading to timeouts, high p95 latency (E13) and increased error count (E11), which directly triggered the three alarms (E2‑E4). (90% confidence)

**memory exhaustion**. Ground truth: The memory_hog_mb flag makes every request allocate 400 MB, more than the function's 256 MB, so the runtime runs out of memory and is killed.

> The recent SSM flag change adding 400 MB of artificial memory usage caused the Lambda (version 12) to run out of memory, producing Runtime.OutOfMemory errors and elevated latency, which triggered the shoplite-5xx-rate alarm. (90% confidence)

**DynamoDB throttling**. Ground truth: The shoplite table's read capacity was lowered from 5 units to 1, so product reads are throttled (ProvisionedThroughputExceededException).

> Capacity reduction of the shoplite DynamoDB table (read capacity set to 1 unit) caused read throttling, which manifested as ProvisionedThroughputExceededException errors in the shoplite‑api Lambda (version 12, sha efc0fd6d7f19) and spiked Lambda error count, triggering both alarms. (95% confidence)

**broken environment variable**. Ground truth: A new version changed only the TABLE_NAME environment variable, to a table that doesn't exist and that ShopLite's role isn't allowed to use, so every DynamoDB call fails with AccessDeniedException (IAM refuses the call before DynamoDB looks for the table).

> Deploy of version 14 changed TABLE_NAME to shoplite-catalogue, but the Lambda’s execution role lacks dynamodb:Query permission on that table, leading to AccessDeniedException errors and the observed 5xx rate and Lambda error alarms. (93% confidence)

## Notes

- The broken-environment-variable ground truth first said ResourceNotFoundException. The logs showed AccessDeniedException: ShopLite's role only covers its own table, so IAM refuses the call before DynamoDB looks for the table. NightWatch's diagnosis was right and the ground truth was corrected after the run. The score is unchanged, because the hypothesis names TABLE_NAME.
- CI redeployed ShopLite as version 12 (a change to the canary) just before the run, so that deploy appears in the first scenario's change history. The error-flag diagnosis mentions version 12 next to the flag change.
- A first attempt the same day was stopped after 2 scenarios. It blamed the bad deploy on the previous scenario's flag change, because a 30-minute log window still held that scenario's errors, and the lab counted one incident twice. Both were fixed (ADR 0013) and all 7 scenarios were run again. The first attempt's partial results are not included.

## How it is scored

A hypothesis names the cause when it contains one of the scenario's phrases (any case; `a+b` means both). The phrases name the cause, not the symptom.

| Scenario | Counts as naming the cause | Right fix |
|---|---|---|
| error flag (error_rate=0.8) | `/shoplite/flags`, `error_rate`, `feature flag` | `reset-feature-flags` |
| bad deploy | `28af2ed` | `rollback-lambda-alias` |
| slow dependency | `extra_latency`, `flag+latency`, `flag+slow`, `flag+delay` | `reset-feature-flags` |
| Lambda timeout | `hang`, `flag+timeout`, `flag+timed out` | `reset-feature-flags` |
| memory exhaustion | `memory_hog`, `flag+memory` | `reset-feature-flags` |
| DynamoDB throttling | `capacity+throttl`, `capacity+lower`, `ProvisionedThroughputExceeded` | `raise-dynamodb-capacity` |
| broken environment variable | `TABLE_NAME`, `environment variable`, `ResourceNotFound+table`, `table+not found`, `table+does not exist`, `table+doesn't exist` | `rollback-lambda-alias` |

Remediation was switched off with the kill switch during the run, so fixes were proposed and recorded but never executed, and nobody was asked to approve them. Approving a fix is measured separately (`make chaos-fix`, see the README).
