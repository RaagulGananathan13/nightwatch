"""The alert-quality coach (F11): noise scores, tuning suggestions, the weekly report."""

from datetime import timedelta
from typing import Any

import pytest

from nightwatch.coach import handler as coach
from nightwatch.coach.stats import AlarmStats, acted_on, alarm_stats, digest, suggest
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Incident
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert
from tests.unit.nightwatch.test_remediation import make_action


def incident(number: int, minutes: float, **extra: Any) -> Incident:
    return Incident.model_validate(
        {
            "id": f"01JB{number:022d}",
            "tenant_id": "default",
            "number": number,
            "service": "shoplite",
            "title": "t",
            "first_alarm": "a",
            "status": "resolved",
            "opened_at": T0,
            "updated_at": T0,
            "last_alert_at": T0,
            "resolved_at": T0 + timedelta(minutes=minutes),
            **extra,
        }
    )


def alerts(*names: str) -> list[dict[str, Any]]:
    return [{"alarm_name": name, "state": "ALARM"} for name in names] + [
        {"alarm_name": name, "state": "OK"} for name in names
    ]


def test_scores_count_pages_that_cleared_by_themselves_and_flaps() -> None:
    stats = alarm_stats(
        [
            (incident(1, 5), alerts("blip"), []),  # cleared by itself in 5 minutes
            (incident(2, 6), alerts("blip"), []),
            (incident(3, 30, acked_by="1", acked_at=T0 + timedelta(minutes=3)), alerts("real"), []),
            (incident(4, 8), alerts("real"), [make_action("verified")]),  # a fix ran
            (incident(5, 4, reopen_count=2), alerts("flappy"), []),
        ]
    )
    assert (stats["blip"].fired, stats["blip"].cleared_by_itself, stats["blip"].noise) == (
        2,
        2,
        100,
    )
    assert (stats["real"].acted_on, stats["real"].noise) == (2, 0)
    assert stats["real"].as_dict()["median_ack_minutes"] == 3.0
    assert stats["flappy"].noise == 100  # 100% quiet + 20 flap points, capped
    assert acted_on(incident(9, 1, fix_pr_url="https://x"), []) is True


@pytest.mark.parametrize(
    ("entry", "config", "starts"),
    [
        (AlarmStats("a", incidents=4, acted_on=3, noise=25), {"EvaluationPeriods": 1}, "Useful"),
        (
            AlarmStats("a", incidents=4, noise=100),
            {"EvaluationPeriods": 1, "Threshold": 2},
            "It pages on a single bad minute",
        ),
        (
            AlarmStats("a", incidents=4, noise=90, flaps=2),
            {"EvaluationPeriods": 2, "DatapointsToAlarm": 2},
            "It flaps",
        ),
        (
            AlarmStats("a", incidents=4, noise=90),
            {"EvaluationPeriods": 2, "DatapointsToAlarm": 2, "Threshold": 5.0},
            "Its pages cleared by themselves",
        ),
        (AlarmStats("a", incidents=4, noise=10), {"EvaluationPeriods": 1}, "Mostly fine"),
        (AlarmStats("a"), None, "Not a CloudWatch alarm"),
    ],
)
def test_suggestions_follow_the_alarms_own_settings(
    entry: AlarmStats, config: dict[str, Any] | None, starts: str
) -> None:
    assert suggest(entry, config).startswith(starts)


def test_digest_names_the_alarms_behind_most_noise() -> None:
    stats = alarm_stats(
        [(incident(n, 5), alerts("blip"), []) for n in range(3)]
        + [(incident(9, 60), alerts("quiet"), [])]
    )
    report = digest(stats)
    assert report["noisy"] == ["blip"]
    assert report["noisy_share"] == 100
    assert report["fired"] == 4
    message = coach.message({**report, "period_days": 7, "incidents": 4})
    assert "1 alarm(s) caused 100% of the pages that cleared by themselves" in message["content"]
    assert message["embeds"][0]["title"] == "blip · noise 100/100"


def test_weekly_run_stores_the_report_for_the_dashboard(
    store: IncidentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nightwatch.api.handler import latest_alarm_report

    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setattr(coach, "utcnow", lambda: T0 + timedelta(hours=1))
    monkeypatch.setattr(coach, "webhook_from_ssm", lambda param, secret: None)

    class CloudWatch:
        def describe_alarms(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "MetricAlarms": [
                    {"AlarmName": n, "EvaluationPeriods": 1} for n in kwargs["AlarmNames"]
                ]
            }

    monkeypatch.setattr(coach, "_cloudwatch", lambda: CloudWatch())
    store.assign_number(store.open_incident(make_alert("shoplite-5xx-rate"), None, T0))
    out = coach.handler({}, None)
    assert out == {"alarms": 1, "noisy": [], "incidents": 1}  # still open: not noise yet
    report = latest_alarm_report(store)
    assert report is not None
    assert report["alarms"][0]["alarm"] == "shoplite-5xx-rate"
    assert report["period_days"] == 7
