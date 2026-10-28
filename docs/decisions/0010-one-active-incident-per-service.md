# 0010. One active incident per service, with optimistic locking

- Status: accepted
- Date: 2026-10-28
- Spec sections: F2, 14 (Phase 2)

## Context

The spec's P0 dedup only merges alerts with the *same* fingerprint inside 10 minutes. Phase 2's
acceptance test needs something stronger: one chaos run trips several *different* alarms
(5xx rate, Lambda errors, latency), and must still produce exactly one incident and one
message.

Those alarms are evaluated on the same minute boundary, so their events reach separate Lambda
invocations within milliseconds. A plain "is there an open incident? if not, create one" check
lets both invocations create one.

## Decision

1. **One active incident per service.** A firing alert joins the service's open incident.
   When every alarm on the incident has recovered, the incident resolves.
2. **Flap window, 10 minutes.** An alarm that fires again within 10 minutes of the incident
   resolving reopens the same incident. A flapping alarm can't spam the channel.
3. **Staleness, 6 hours.** An open incident with no new firing alert for 6 hours stops
   collecting alerts, so one stuck incident can't swallow unrelated problems.
4. **Optimistic locking.** A per-service pointer item (`OPEN#<service>`) names the current
   incident and carries a version number. Every change is a single DynamoDB transaction that:
   - writes the alert, with a condition that makes repeated deliveries harmless
   - updates the incident
   - bumps the pointer's version, with a condition that it still holds the value we read

   If another invocation changed the incident first, the transaction is cancelled and we read
   again and decide again. The loser of a race therefore joins the winner's incident.
5. **Numbers after opening.** The winner assigns the `INC-###` number after its transaction
   succeeds, so losing a race never wastes a number.
6. **One message per incident.** The opener posts the Discord message and the incident
   remembers its id. Every later change edits that message in place ("3 alerts grouped into
   1 incident", then "resolved").

## Consequences

- The spec's alert-fatigue promise is visible in chat: N alerts become one edited message.
- The correlation logic is a small, pure decision plus one transaction. Unit tests cover the
  race, duplicate deliveries, reopening and staleness against moto.
- Grouping is by service only. P1 adds dependency-aware grouping (for example, a DynamoDB
  throttling alarm on a table the service uses) through `services.yaml`.
