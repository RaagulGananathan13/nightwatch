# 0022. The Slack adapter: one set of cards, two chats

- Status: accepted
- Date: 2026-11-01
- Spec sections: F5, F9, section 11, Phase 8

## Context

Discord is NightWatch's default chat. The spec also wants Slack: the same incident cards with
diagnosis and approval buttons, `/nw` commands, and an HMAC check on every request from Slack.
Writing every card and handler twice would let the two chats drift apart, and every new
feature would cost twice.

## Decision

1. **One card format, translated.** NightWatch keeps building every card in Discord's shape
   (`cards.py`). `slack.to_slack` turns any of them into Slack Block Kit:
   - content becomes a section;
   - each embed becomes a colored attachment with its title, description, fields and footer;
   - buttons become Slack buttons with the same ids.

   Incident cards, approval cards, escalations and drafted status updates therefore look the
   same in both chats.
2. **One endpoint, two signatures.** Slack's interactivity and slash-command URLs point at the
   same Lambda URL as Discord's.
   - A request with `X-Slack-Signature` is checked with HMAC-SHA256 over its timestamp and body,
     using the app's signing secret, and refused when more than 5 minutes old.
   - The request is then reshaped into Discord's form and goes through the same handlers:
     approve/reject, publish/discard, and `/nw status`, `ack`, `ask` and `update`.
   - A click answers through Slack's response URL. A slow command (`ask`, `update`) gets its
     answer posted there by the ask or updates Lambda, which picks Discord or Slack from the job.
3. **Slack mirrors Discord.**
   - Wherever NightWatch posts or edits the incident card (ingest, report, postmortem, cost
     guardian), it does the same in Slack. The incident remembers its Slack message (`slack_ts`).
   - Approval cards go into the incident's Slack thread, ping the Slack approvers, and are
     edited as the fix moves on. Approvers for each chat are listed separately in
     `services.yaml` (`approvers` and `slack_approvers`), and one check accepts either.
   - Only an opened incident posts a new Slack card. Later changes only edit it, so two alerts
     arriving together can't post two cards.
4. **Nothing from an alarm can ping.** Before conversion every `&`, `<` and `>` is escaped, so
   `<!channel>` in an alarm's text arrives as text. Discord pings are dropped; Slack pings are
   added only for NightWatch's own approvers. Discord's `<t:...>` timestamps become Slack dates.
5. **Off until configured.** The adapter switches on when `slack_channel_id` is set. The owner
   stores the bot token and signing secret in SSM by hand, as with Discord, so neither passes
   through Terraform or the chat. A Slack failure is logged and never stops the Discord side.

## Consequences

- Free: Slack's free plan and two SecureString parameters. The Lambdas and URL are shared.
- A decision taken in one chat updates the card there at once. The other chat's copy catches up
  when the fix's next step edits both; until then, its buttons answer that the fix is already
  decided.
- The forecast and coach digests stay in Discord only, to keep each Lambda's reach small.
- Slack's slash-command responses can't be edited in place, so a slow answer appears as a new
  message under a "thinking" note that only the asker sees.
