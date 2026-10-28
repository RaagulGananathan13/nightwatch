"""Event normalisation and the small shared helpers."""

from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from nightwatch.common.config import Settings
from nightwatch.common.ids import new_ulid
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import from_iso, to_iso
from nightwatch.ingest.normalize import from_cloudwatch_event
from tests.unit.nightwatch.conftest import T0, alarm_event

TAGS = {"nightwatch:service": "shoplite", "nightwatch:severity": "High"}


def test_alarm_event_becomes_an_alert() -> None:
    alert = from_cloudwatch_event(alarm_event(), lambda arn: TAGS)
    assert alert is not None
    assert alert.service == "shoplite"
    assert alert.state == "ALARM"
    assert alert.previous_state == "OK"
    assert alert.severity == "high"
    assert alert.occurred_at == T0
    assert alert.alarm_arn is not None
    assert alert.alarm_arn.endswith(":alarm:shoplite-5xx-rate")
    assert [m.name for m in alert.metrics] == ["Url5xxCount"]  # expressions are skipped
    again = from_cloudwatch_event(alarm_event(), lambda arn: TAGS)
    assert again is not None
    assert alert.fingerprint == again.fingerprint


def test_untagged_alarms_belong_to_someone_else() -> None:
    assert from_cloudwatch_event(alarm_event(), lambda arn: {}) is None


def test_insufficient_data_and_foreign_events_are_ignored() -> None:
    event = alarm_event()
    event["detail"]["state"]["value"] = "INSUFFICIENT_DATA"
    assert from_cloudwatch_event(event, lambda arn: TAGS) is None
    assert from_cloudwatch_event({"source": "aws.ec2"}, lambda arn: TAGS) is None


def test_unknown_severity_counts_as_medium() -> None:
    alert = from_cloudwatch_event(alarm_event(), lambda arn: {**TAGS, "nightwatch:severity": "?"})
    assert alert is not None
    assert alert.severity == "medium"


def test_ulids_are_unique_and_sort_by_time() -> None:
    early, late = new_ulid(T0), new_ulid(T0 + timedelta(milliseconds=1))
    assert len(early) == 26
    assert early < late
    assert new_ulid(T0) != new_ulid(T0)


def test_iso_round_trip_and_cloudwatch_offsets() -> None:
    moment = datetime(2026, 10, 28, 10, 54, 1, 123000, tzinfo=UTC)
    assert to_iso(moment) == "2026-10-28T10:54:01.123Z"
    assert from_iso(to_iso(moment)) == moment
    assert from_iso("2026-10-28T10:54:01.123+0000") == moment


def test_settings_come_from_the_environment() -> None:
    settings = Settings.from_env({"TABLE_NAME": "t", "FLAP_WINDOW_MINUTES": "5"})
    assert settings.table_name == "t"
    assert settings.flap_window == timedelta(minutes=5)
    assert settings.discord_webhook_param == ""


@mock_aws
def test_secret_cache_decrypts_once_per_ttl() -> None:
    ssm = boto3.client("ssm")
    ssm.put_parameter(Name="/nightwatch/x", Value="secret-1", Type="SecureString")
    now = [0.0]
    cache = SecretCache(ttl_seconds=60, clock=lambda: now[0])
    assert cache.get("/nightwatch/x") == "secret-1"
    ssm.put_parameter(Name="/nightwatch/x", Value="secret-2", Type="SecureString", Overwrite=True)
    assert cache.get("/nightwatch/x") == "secret-1"  # cached
    now[0] = 61
    assert cache.get("/nightwatch/x") == "secret-2"


def test_secret_cache_needs_a_name() -> None:
    with mock_aws(), pytest.raises(Exception, match="ParameterNotFound"):
        SecretCache().get("/nightwatch/missing")
