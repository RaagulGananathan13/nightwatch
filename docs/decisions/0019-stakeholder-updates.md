# 0019. Stakeholder updates: drafted by NightWatch, published by a person

- Status: accepted
- Date: 2026-10-30
- Spec sections: F13 (Phase 8)

## Context

During an incident, the on-call engineer also has to tell two other audiences what is going
on:
- customers, who need plain words and no internal detail;
- leaders, who need the impact, the likely cause and what is being done.

Writing both while fixing the problem is slow, and a hurried public update can leak internal
names or guess at a cause. The spec asks for one click to draft both, and for the customer
update to reach a public status page only after a person approves it.

## Decision

1. **A slash command, not a button on the incident card.** `/nw update incident:INC-12`
   drafts the updates for an open or resolved incident. The incident card is posted and
   edited through a channel webhook, and Discord only allows interactive buttons on messages
   an application owns. Moving the card to the bot would change four places that edit it.
   The command's reply is owned by the application, so it can carry the buttons.
2. **Drafting runs in its own Lambda.** The interactions Lambda answers Discord at once with
   "NightWatch is thinking...", then starts `nightwatch-updates`, as `/nw ask` does. That
   Lambda runs under the investigator's read-only role. It writes both texts with one LLM
   call, from facts NightWatch recorded:
   - the diagnosis summary and its impact;
   - the top hypothesis, only if the evidence guard verified it;
   - who has the incident, and any fix pull request.

   Fixed rules write both texts when no model answers.
3. **Internal detail never reaches customers.** If the customer draft names a function,
   table, parameter, log group, alarm or repository, it is replaced by the rules' plain
   version, and a note says what was found. The same applies to cloud jargon ("Lambda",
   "DynamoDB", "throttled"), an error class (`KeyError`) or a commit SHA. The product's own
   public name (`public_name: ShopLite` in `services.yaml`) is allowed. The leadership summary
   is internal and keeps the model's wording.
4. **A person publishes, once.** The drafts appear in Discord with **Publish to status page**
   and **Discard** buttons. Only the service's approvers can use them. A conditional write lets
   each draft be decided once, so a double click can't publish twice. Every request, draft,
   refusal and decision goes to the audit log.
5. **The status page is a static file on the dashboard's CloudFront.** Publishing rewrites
   `status/index.html` in the dashboard's bucket from the 20 newest published updates:
   - a banner saying whether any incident's latest update is still "investigating";
   - each update, its time and its state.

   It has no scripts. Its content security policy allows only its own stylesheet, by hash.
   It is cached for 60 seconds, so no CloudFront invalidation is needed. The dashboard deploy
   leaves `status/` alone. The interactions role may write only `status/*` in that bucket.

## Consequences

- No new paid parts: one more small Lambda, a few table items, one S3 object on an existing
  distribution.
- The page changes only when someone publishes. When an incident is over, someone has to
  publish the all-clear (`/nw update` on a resolved incident drafts it). Until then the
  banner still says the problem is ongoing.
- The internal-detail check is a list of names and patterns, not understanding. It catches
  the leaks that matter most here (resource names, commits, jargon), and the approver reads
  every word before it goes out.
- Leaders get the summary through whoever forwards it from Discord; NightWatch doesn't email.
