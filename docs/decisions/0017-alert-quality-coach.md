# 0017. The alert-quality coach: a noise score anyone can check

- Status: accepted
- Date: 2026-10-30
- Spec sections: F11, F18 (Phase 8)

## Context

Alert fatigue is the problem NightWatch exists for. An alarm that pages people for blips
teaches them to ignore pages. Knowing which alarms are noise needs data: how often each one
fired, and whether anyone had to act.

## Decision

1. **Score from records NightWatch already keeps.** The incidents, their alerts, the actions
   and the acknowledgements are all in the table, so no extra telemetry is needed. Per alarm,
   over the last 7 days:
   - **fired:** how many times it went to ALARM;
   - **led to action:** incidents with a fix that ran or was decided, an acknowledgement, or a
     fix pull request;
   - **cleared by itself:** incidents that resolved within 15 minutes with nobody acting;
   - **flaps:** incidents that reopened.
2. **A score you can recompute by hand.** The score is the share of the alarm's incidents that
   cleared by itself, plus 10 points per flap, up to 100. There are no weights to tune and no
   model.
3. **Suggestions from the alarm's own settings.** The coach reads each alarm from CloudWatch:
   - **One bad minute is enough to page** (1 datapoint): suggest "2 of 3".
   - **It flaps:** suggest a longer window.
   - **Its pages clear by themselves:** suggest a higher threshold, or asking whether it should
     page at all.
   - **Most of its pages led to action:** say "keep it".
4. **Weekly, everywhere.** EventBridge Scheduler runs the coach every Monday at 08:00 UTC, and
   `make coach` runs it on demand. The report goes three places:
   - a 180-day item in the table;
   - the dashboard's **Alarms** page;
   - a Discord digest: "N alarm(s) caused X% of the pages that cleared by themselves", with a fix
     for each.
5. **No automatic tuning pull requests yet.** The spec makes it optional for the coach to open
   pull requests with alarm changes (reusing F10). That would mean adding the alarms'
   `evaluation_periods` and `datapoints_to_alarm` to the Terraform catalogue. It is a small
   follow-up, left until the suggestions have proved right over a few weeks.

## Consequences

- One cheap Lambda run a week: free.
- The score treats every page that cleared by itself as noise. During chaos experiments that
  is literally true, because the lab undoes its own fault. Read the report knowing what ran
  that week.
