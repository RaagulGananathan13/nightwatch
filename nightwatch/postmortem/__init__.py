"""Blameless postmortems and incident memory, written when an incident resolves (F6, F8)."""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.common.log import emit


def start(function_name: str, incident_id: str) -> bool:
    """Ask the postmortem Lambda to close out an incident, without waiting for it.

    Returns False when postmortems are off or the request failed (the incident is fine either
    way; ``make backfill-memory`` can write it later).
    """
    if not function_name:
        return False
    try:
        boto3.client("lambda").invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps({"incident_id": incident_id}).encode(),
        )
    except (BotoCoreError, ClientError) as exc:
        emit("ERROR", "could not start the postmortem", incident=incident_id, error=str(exc)[:200])
        return False
    return True
