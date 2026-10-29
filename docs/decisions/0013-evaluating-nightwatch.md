# 0013. How NightWatch is evaluated

- Status: accepted
- Date: 2026-10-29
- Spec sections: F16, 10.3, 17 (Phase 5)

## Context

NightWatch's headline claims are root-cause accuracy and time to diagnosis. They are only worth
something if they are measured on real failures, the same way every time, and published with
enough detail that a reader can check them. The spec says to fill in real numbers and never
invent them.

## Decision

1. **Seven scenarios, each a real fault in the live stack** (see the
   [chaos lab](../../chaos/README.md)):
   - a buggy commit;
   - a bad feature flag;
   - a slow dependency;
   - a Lambda timeout;
   - memory exhaustion;
   - DynamoDB throttling after a capacity cut;
   - a broken environment variable.

   Each has a written ground truth and the fix that would solve it.
2. **Cause, not symptom.** A diagnosis counts as correct when its hypothesis contains one of
   the scenario's phrases. The phrases name the cause, for example "the hang flag" or "capacity
   ... lowered", never only the symptom ("requests time out"). `a+b` means both parts must
   appear. Top-1 checks the top hypothesis; top-3 checks the first three.
3. **Plain metrics, defined once** in `eval/score.py`:
   - time from the alarm to the diagnosis in Discord, and from the fault to the diagnosis;
   - evidence validity, meaning hypotheses the guard verified, with and without its citation
     repair;
   - the fix proposed;
   - LLM calls and tokens.
4. **Read-only runs.** The kill switch is on for the whole evaluation. Fixes are planned and
   scored, but never run and never sent to a human. Approving a fix is tested separately with
   `make chaos-fix`.
5. **Everything is published.** `eval/results.md` shows every top hypothesis word for word,
   next to the phrases it was scored on. `eval/results.json` keeps the raw numbers. Both record
   the commit, prompt version and model, so prompt versions can be compared (spec 10.3).
6. **Better evidence for the new failure types.** These were added so the evidence can show
   these failures at all:
   - **Lambda REPORT lines.** Every investigation now queries Lambda's own REPORT lines, giving
     invocations, p95 and maximum duration, and peak against allowed memory, per function
     version. Without them, a timeout or an out-of-memory crash shows up only as a generic
     error.
   - **What a deploy changed.** "What changed" now compares each deploy with the version it
     replaced: new code, or the same code with changed environment variables. It names the
     variables only, never their values.

## Consequences

- Phrase matching is simple and transparent, but it can be wrong in both directions. A
  hypothesis could use the phrase while blaming something else, or describe the cause in other
  words. That is why every scored hypothesis is printed in full for the reader to judge.
- Each scenario runs once per evaluation, so seven runs can't support fine-grained claims.
  Repeat runs (`--only ...`) and report the spread before claiming small differences.
- In five of the seven scenarios the cause is visible in the change history (flags, capacity,
  deploys). That is realistic, because most production incidents follow a change. The test is
  whether the agent connects the right change to the symptom and names it.
- A full run takes about 2.5 hours, mostly waiting out the flap window between incidents.
