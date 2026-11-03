# Demo script (2 minutes)

The spec's demo: a fault goes in, a diagnosis with evidence comes out, a person approves, the
service recovers, and a postmortem is written. Record it in one take, then speed up the waits.

## Before you record

- **Set up the screen.** Open four browser tabs: Slack `#incidents`, the dashboard, the GitHub
  README, and the eval results. Open a terminal in the repository.
- **Check the lab.** No incident should be open, and the kill switch must be off: `/nw status`
  should say there are no open incidents.
- **Record with a free tool.** Use [OBS Studio](https://obsproject.com/), or Windows' own
  recorder (**Win + Alt + R**), at 1920×1080. For the README GIF, use
  [ScreenToGif](https://www.screentogif.com/).

## Shots

| Time | On screen | Say |
|---|---|---|
| 0:00 | The README's top lines | "NightWatch is an AI on-call agent for AWS. It runs on free tiers: zero dollars a month." |
| 0:10 | Terminal: `python -m chaos bad-deploy --let-nightwatch-fix` | "I'll ship a buggy commit to this demo store and see what happens." |
| 0:20 | Slack: the incident card appears (speed up the wait) | "About a minute later, NightWatch has grouped the alarms into one incident…" |
| 0:30 | The card's diagnosis: version 18, `KeyError: 'price'` | "…and found the cause: the new version reads a field that doesn't exist. Every claim cites its evidence." |
| 0:45 | Dashboard → the incident → click an `E` item | "Each piece of evidence is the exact query and result the agent ran. A guard marks anything it can't back up as unverified." |
| 1:00 | Slack thread: the approval card, then **Approve** | "It proposes a rollback from a reviewed runbook. Nothing runs until an approver clicks." |
| 1:10 | The card: running, then verified | "It rolls back, then checks the error rate stayed at zero. If things had got worse, it would undo the fix by itself." |
| 1:25 | The incident card turns green: resolved | "Resolved, about five minutes from the bad deploy." |
| 1:35 | Dashboard → the postmortem | "Every incident gets a blameless postmortem: timeline, cause, fix and lessons." |
| 1:50 | The eval results table | "On seven injected faults it found every root cause, in 46 seconds on average. It's open source; the link is below." |

## The README GIF

Make a 10–15 second GIF of shots 0:30 to 1:10: the diagnosis, the **Approve** click and the
card turning verified. Save it as `docs/assets/demo.gif`; the README shows it under the status.

## Afterwards

The chaos script puts ShopLite back either way. The incident's postmortem is the demo's own
record, so leave it in.
