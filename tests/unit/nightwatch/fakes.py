"""A recorded "bad deploy" world for the investigation tools: no network, no real AWS.

ShopLite's version 5 (commit 3f2a9c1e4b5d) went live two minutes before the incident and
every failing request since raises KeyError. That is the same shape as the chaos lab's bad
deploy.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

T0 = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
BAD_SHA = "3f2a9c1e4b5d"
GOOD_SHA = "860c8bb9f659"


def lambda_time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}+0000"


class Paginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages

    def paginate(self, **_: Any) -> list[dict[str, Any]]:
        return self.pages


class FakeLambda:
    def get_alias(self, **_: Any) -> dict[str, Any]:
        return {"FunctionVersion": "5"}

    def get_paginator(self, name: str) -> Paginator:
        versions = [
            {"Version": "$LATEST", "LastModified": lambda_time(T0), "Description": ""},
            {
                "Version": "4",
                "LastModified": lambda_time(T0 - timedelta(hours=3)),
                "Description": f"sha={GOOD_SHA} run=1 by=RaagulGananathan13",
            },
            {
                "Version": "5",
                "LastModified": lambda_time(T0 - timedelta(minutes=2)),
                "Description": f"sha={BAD_SHA} by=chaos-lab: read prices from the new price field",
            },
        ]
        return Paginator([{"Versions": versions}])

    def get_function_configuration(self, **_: Any) -> dict[str, Any]:
        return {
            "Version": "5",
            "Description": f"sha={BAD_SHA} by=chaos-lab",
            "Runtime": "python3.13",
            "Architectures": ["arm64"],
            "MemorySize": 256,
            "Timeout": 5,
            "Environment": {
                "Variables": {"TABLE_NAME": "shoplite", "API_TOKEN": "not-a-real-token"}
            },
            "LastModified": lambda_time(T0 - timedelta(minutes=2)),
            "State": "Active",
            "LastUpdateStatus": "Successful",
        }

    def get_function_concurrency(self, **_: Any) -> dict[str, Any]:
        return {}


class FakeLogs:
    """Answers the three standard queries with the bad-deploy picture."""

    ROWS: ClassVar[dict[str, list[dict[str, str]]]] = {
        "errors_by_version": [{"error_type": "KeyError", "version": BAD_SHA, "errors": "57"}],
        "requests_by_version": [
            {"version": GOOD_SHA, "level": "INFO", "requests": "160"},
            {"version": BAD_SHA, "level": "ERROR", "requests": "57"},
            {"version": BAD_SHA, "level": "INFO", "requests": "12"},
        ],
        "runtime_errors": [{"error": "[ERROR] KeyError: 'price'", "count": "57"}],
    }

    def __init__(self, extra_rows: list[dict[str, str]] | None = None) -> None:
        self.queries: list[str] = []
        self.extra_rows = extra_rows or []

    def start_query(self, **kwargs: Any) -> dict[str, str]:
        self.queries.append(kwargs["queryString"])
        return {"queryId": str(len(self.queries))}

    def get_query_results(self, **kwargs: Any) -> dict[str, Any]:
        query = self.queries[int(kwargs["queryId"]) - 1]
        if "error_type, version" in query:
            rows = self.ROWS["errors_by_version"]
        elif "requests by version" in query:
            rows = self.ROWS["requests_by_version"]
        elif "Runtime exited" in query:
            rows = self.ROWS["runtime_errors"]
        else:
            rows = self.extra_rows
        results = [[{"field": k, "value": v} for k, v in row.items()] for row in rows]
        return {"status": "Complete", "results": results}

    def stop_query(self, **_: Any) -> dict[str, Any]:
        return {}


class FakeCloudWatch:
    def describe_alarms(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "MetricAlarms": [
                {
                    "AlarmName": kwargs["AlarmNames"][0],
                    "AlarmDescription": "shoplite Lambda failed 2 or more times in a minute.",
                    "Namespace": "AWS/Lambda",
                    "MetricName": "Errors",
                    "Statistic": "Sum",
                    "Dimensions": [{"Name": "FunctionName", "Value": "shoplite-api"}],
                    "ComparisonOperator": "GreaterThanOrEqualToThreshold",
                    "Threshold": 2.0,
                    "EvaluationPeriods": 1,
                    "DatapointsToAlarm": 1,
                    "StateValue": "ALARM",
                    "StateReason": "Threshold Crossed: 1 datapoint [15.0] >= 2.0",
                }
            ]
        }

    def describe_alarm_history(self, **_: Any) -> dict[str, Any]:
        return {
            "AlarmHistoryItems": [
                {"Timestamp": T0, "HistorySummary": "Alarm updated from OK to ALARM"}
            ]
        }

    def get_metric_statistics(self, **_: Any) -> dict[str, Any]:
        before = [{"Timestamp": T0 - timedelta(minutes=m), "Sum": 0.0} for m in range(30, 5, -1)]
        during = [{"Timestamp": T0 + timedelta(minutes=m), "Sum": 15.0} for m in range(-1, 3)]
        return {"Datapoints": before + during}


class FakeDynamo:
    def __init__(self, lowered_at: datetime | None = None) -> None:
        self.lowered_at = lowered_at

    def describe_table(self, **_: Any) -> dict[str, Any]:
        throughput: dict[str, Any] = {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5}
        if self.lowered_at:
            throughput["LastDecreaseDateTime"] = self.lowered_at
            throughput["WriteCapacityUnits"] = 1
        table = {"TableStatus": "ACTIVE", "ProvisionedThroughput": throughput, "ItemCount": 40}
        return {"Table": table}


class FakeSsm:
    def __init__(self, history: list[dict[str, Any]] | None = None) -> None:
        self.history = history or []

    def get_paginator(self, name: str) -> Paginator:
        return Paginator([{"Parameters": self.history}])


class FakeCloudTrail:
    def lookup_events(self, **_: Any) -> dict[str, Any]:
        return {"Events": []}


class FakeAws:
    """Read-only fakes only: any write call would fail with AttributeError."""

    def __init__(self, **overrides: Any) -> None:
        self.lambda_ = overrides.get("lambda_", FakeLambda())
        self.logs = overrides.get("logs", FakeLogs())
        self.cloudwatch = overrides.get("cloudwatch", FakeCloudWatch())
        self.dynamodb = overrides.get("dynamodb", FakeDynamo())
        self.ssm = overrides.get("ssm", FakeSsm())
        self.cloudtrail = overrides.get("cloudtrail", FakeCloudTrail())
