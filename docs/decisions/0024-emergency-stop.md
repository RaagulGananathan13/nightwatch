# 0024. The emergency stop: switch everything off before it costs money

- Status: accepted
- Date: 2026-11-03
- Spec sections: F17, section 11 (after v1.0)

## Context

NightWatch's measured spend is about one cent a month: $0.01 in October 2026, by the AWS
budget. The cost guardian ([ADR 0021](0021-cost-guardian.md)) turns cost signals into
incidents, but an incident only tells someone. If spend ever runs away, for example from a
flood of requests on a public function URL or a bug that loops, nothing switched off. The owner
asked for exactly that: when cost rises, everything stops.

AWS Budgets can't stop anything by itself, and its numbers lag by up to a day. A flood can
burn through the free 1 million Lambda requests in a few hours, so a budget alone is too slow.

## Decision

1. **Three triggers.** Each starts the cost Lambda, which runs the stop
   (`nightwatch/cost/stop.py`):
   - **Real spend at or past $0.50 this month**, about 50 times a normal month. The budget gets
     a fourth alert, at 50% of its $1 limit, on the SNS topic the cost guardian already
     reads. Only real (ACTUAL) spend stops anything. Forecasts are often wrong early in a
     month, so they only open an incident.
   - **Lambda calls running away.** The `nightwatch-runaway-invocations` alarm fires at more
     than 30,000 calls in 5 minutes, across the account. The busiest 5 minutes so far were
     5,355 calls, during a load drill on 2026-10-30. The alarm calls the cost Lambda directly,
     so the stop starts within minutes. It is the 8th of the 10 free alarm metrics, and the
     free-tier guard counts it.
   - **The morning check finds money already being spent**: a free tier used up this month,
     or DynamoDB capacity past the free 25 units. A jump or a forecast still only warns.
2. **What the stop does**, in this order, each step tried even if an earlier one failed:
   1. tells Discord and Slack why;
   2. turns the kill switch on, so no fix runs until a person turns it off;
   3. shrinks DynamoDB tables provisioned past the free tier to 1 unit each;
   4. gives every `nightwatch-*` and `shoplite-*` Lambda a reserved concurrency of 0, itself
      last.

   A reserved concurrency of 0 is AWS's off switch for a function. Every invocation is
   refused, from function URLs, EventBridge, SNS and Step Functions alike, and refused
   invocations aren't billed. Nothing is deleted: data, logs and settings stay.
3. **Narrow rights.** The cost Lambda's role may set concurrency only on `nightwatch-*` and
   `shoplite-*` functions, write only the kill switch, and update only NightWatch's and the
   watched tables. It can't switch anything back on.
4. **A person switches it back on.** `make resume` (`scripts/resume.py`), run as the deployer,
   removes the zero concurrency and turns the kill switch off. If tables were shrunk,
   `make apply` sets their capacity from Terraform again. NightWatch never resumes by itself:
   someone should check the bill first.

## Consequences

- Free: one alarm metric (8 of 10), one more alert on the existing budget, and a few free API
  calls when the stop runs.
- While stopped, NightWatch is off too: no alerts and no chat. That is deliberate. For this
  project a monitor that costs money is worse than no monitor, and chat is told first.
- A slow leak, under 30,000 calls in 5 minutes, is caught by the $0.50 line or the morning
  check. Because the budget lags, spend could pass $0.50 before the stop.
- Some things can't be switched off this way: S3 and DynamoDB storage (a few MB) and
  CloudFront, all inside the always-free tier, and anything not named `nightwatch-*` or
  `shoplite-*`. Another project's functions in the same account are never touched.
- Tested in `test_stop.py`: the order of the steps, that one failure stops nothing else, that
  only real spend past the line stops, the alarm, the morning check, and that `make resume`
  switches back on only what the stop switched off.
