# Glossary

Terms used throughout the NightWatch documentation, in alphabetical order.

Alarm
:   A CloudWatch alarm on a watched service. Alarms are NightWatch's main input.

Approver
:   A Discord or Slack user listed for a service in the service catalogue. Only approvers can
    accept or reject a proposed fix, and every refusal is audited.

Audit log
:   The record of every proposal, approval, fix, verification and emergency stop, with who did
    it and when. It is kept in NightWatch's DynamoDB table.

Connected account
:   An AWS account other than the one NightWatch runs in. NightWatch reads it through a
    read-only role, assumed with an ExternalId. See [Connect an AWS account](../connect.md).

Cost guardian
:   The daily usage check and the AWS budget alerts. Each cost signal becomes an incident,
    like an alarm. See [ADR 0021](../decisions/0021-cost-guardian.md).

Emergency stop
:   The last cost control. When real spend reaches $0.50 in a month, or Lambda calls run away,
    it switches every NightWatch and ShopLite function off until a person runs `make resume`.
    See [Cost and safety](../cost-and-safety.md#the-emergency-stop).

Evidence ledger
:   The facts gathered during an investigation, each with its source. A diagnosis can cite
    only facts in the ledger.

Guard
:   The check that compares every number, version and identifier in a diagnosis with the
    evidence ledger. A claim the evidence does not support is marked unverified.

Incident
:   One problem on one service. Further alarms on the same service join the open incident
    instead of opening new ones.

Kill switch
:   The SSM parameter `/nightwatch/kill_switch`. While it is on, or cannot be read, no fix runs.

Permissions boundary
:   The `NightWatchBoundary` IAM policy. It caps what every NightWatch role can do, whatever
    the role's own policy allows.

Postmortem
:   The blameless report written when an incident resolves. It is stored in S3 and added to
    incident memory, so a repeat failure is linked to the earlier one.

Runbook
:   A reviewed YAML file in `runbooks/` that describes one fix: when it applies, its
    parameters, and the AWS calls it makes.

Service catalogue
:   `nightwatch/services.yaml`. It lists each watched service with its functions, tables,
    parameters and approvers.

ShopLite
:   The demo application that NightWatch watches in the reference deployment, and that the
    chaos scenarios break on purpose.

Tenant
:   The owner of a set of incidents. Every DynamoDB key starts with `TENANT#<tenant>`, and IAM
    holds each role to its own tenant's keys.

Verification
:   The check after a fix that the alarm's metrics recovered. If they got worse, the fix is
    undone automatically.
