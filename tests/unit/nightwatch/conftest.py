"""A moto copy of the nightwatch table, plus builders for alarm events and alerts."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Alert

TABLE = "nightwatch-test"
T0 = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)


def create_table(name: str = TABLE) -> None:
    key: list[Any] = [
        {"AttributeName": n, "AttributeType": "S"} for n in ("PK", "SK", "GSI1PK", "GSI1SK")
    ]
    boto3.client("dynamodb").create_table(
        TableName=name,
        BillingMode="PROVISIONED",
        ProvisionedThroughput={"ReadCapacityUnits": 8, "WriteCapacityUnits": 8},
        AttributeDefinitions=key,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {"ReadCapacityUnits": 2, "WriteCapacityUnits": 2},
            }
        ],
    )


@pytest.fixture
def aws() -> Iterator[None]:
    with mock_aws():
        create_table()
        yield


@pytest.fixture
def store(aws: None) -> IncidentStore:
    return IncidentStore(TABLE, "default")


def make_alert(
    alarm: str = "shoplite-5xx-rate",
    state: str = "ALARM",
    at: datetime = T0,
    service: str = "shoplite",
    severity: str = "medium",
    alert_id: str | None = None,
) -> Alert:
    return Alert.model_validate(
        {
            "id": alert_id or f"{alarm}-{state}-{at.isoformat()}",
            "source": "cloudwatch",
            "service": service,
            "alarm_name": alarm,
            "state": state,
            "severity": severity,
            "description": f"{alarm} crossed its threshold",
            "fingerprint": alarm,
            "occurred_at": at,
        }
    )


def alarm_event(
    alarm: str = "shoplite-5xx-rate",
    state: str = "ALARM",
    at: datetime = T0,
    event_id: str | None = None,
) -> dict[str, Any]:
    """An EventBridge "CloudWatch Alarm State Change" event, shaped like the real thing."""
    stamp = at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+0000"
    return {
        "version": "0",
        "id": event_id or f"evt-{alarm}-{state}-{int(at.timestamp())}",
        "detail-type": "CloudWatch Alarm State Change",
        "source": "aws.cloudwatch",
        "account": "111122223333",
        "time": (at + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "region": "us-east-2",
        "resources": [f"arn:aws:cloudwatch:us-east-2:111122223333:alarm:{alarm}"],
        "detail": {
            "alarmName": alarm,
            "configuration": {
                "description": "More than 5% of shoplite requests returned 5xx in a minute.",
                "metrics": [
                    {
                        "id": "errors",
                        "metricStat": {
                            "metric": {
                                "namespace": "AWS/Lambda",
                                "name": "Url5xxCount",
                                "dimensions": {"FunctionName": "shoplite-api"},
                            },
                            "period": 60,
                            "stat": "Sum",
                        },
                        "returnData": False,
                    },
                    {"id": "rate", "expression": "100 * FILL(errors, 0) / requests"},
                ],
            },
            "previousState": {"value": "OK" if state == "ALARM" else "ALARM", "timestamp": stamp},
            "state": {
                "value": state,
                "reason": "Threshold Crossed: 1 datapoint",
                "timestamp": stamp,
            },
        },
    }
