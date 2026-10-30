"""Write the postmortem and memory of incidents that resolved before Phase 6.

    python -m scripts.backfill_memory            # every resolved incident without a postmortem
    python -m scripts.backfill_memory --all      # rewrite them all

It asks the postmortem Lambda to close out each incident (one LLM call each for the lessons,
counted against the daily LLM budget), a couple of seconds apart.
"""

from __future__ import annotations

import argparse
import json
import time

import boto3

from nightwatch.common.incidents import IncidentStore

FUNCTION = "nightwatch-postmortem"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="also rewrite existing postmortems")
    parser.add_argument("--profile", default="nightwatch")
    parser.add_argument("--table", default="nightwatch")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name="us-east-2")
    store = IncidentStore(args.table, "default", session.client("dynamodb"))
    incidents = sorted(store.list_incidents("resolved", limit=200), key=lambda i: i.opened_at)
    todo = [i for i in incidents if args.all or not i.postmortem_key]
    client = session.client("lambda")
    for incident in todo:
        client.invoke(
            FunctionName=FUNCTION,
            InvocationType="Event",
            Payload=json.dumps({"incident_id": incident.id}).encode(),
        )
        print(f"queued {incident.label}")  # noqa: T201
        time.sleep(2)  # stay well inside the LLM's per-minute limits
    print(f"{len(todo)} postmortem(s) queued; they appear in S3 within a minute.")  # noqa: T201


if __name__ == "__main__":
    main()
