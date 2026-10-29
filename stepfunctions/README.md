# Incident workflow

`incident.asl.json` is the Step Functions state machine `nightwatch-incident`. Terraform fills
in the Lambda ARNs. The ingest Lambda starts one execution per new incident, named after the
incident's id, so a retried event can never start a second one.

| Step | What happens |
|---|---|
| LetAlertsSettle | Waits 30 s so related alarms join the incident first. Waiting costs nothing. |
| Investigate | `nightwatch-investigate` gathers evidence, asks the LLM, and checks the answer (spec F3, F4) |
| Report | `nightwatch-report` adds the diagnosis to the incident's Discord message |
| ReportFailure | If the investigation fails, the message says so instead of staying silent |

Each run uses about 5 state transitions; the free tier allows 4,000 a month. The execution
history is kept for 90 days for free, so you can see every incident's run in the console.
Phase 4 adds approval, remediation and verification after Report.
