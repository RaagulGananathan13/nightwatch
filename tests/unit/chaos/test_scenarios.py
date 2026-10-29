"""Every scenario injects its fault, is always undone, and is judged on naming the cause."""

import pytest

from chaos.runner import mentions, run
from chaos.scenarios import SCENARIOS
from tests.unit.chaos.test_runner import SHA, FakeLab, FakeTime

# A diagnosis that names each scenario's real cause.
GOOD = {
    "error-flag": "The feature flag /shoplite/flags set error_rate to 0.8.",
    "bad-deploy": f"Deploy {SHA} broke listings.",
    "dependency-latency": "The extra_latency_ms flag slows every request.",
    "lambda-timeout": "The hang flag makes every request time out.",
    "memory-exhaustion": "The memory_hog_mb flag exhausts the function's memory.",
    "dynamodb-throttle": "Read capacity was lowered to 1, so reads are throttled.",
    "broken-env-var": "Version 6 changed TABLE_NAME to a table that does not exist.",
}
# Right symptom, wrong cause: these must not count.
SYMPTOM_ONLY = {
    "dependency-latency": "p95 latency is above the threshold.",
    "lambda-timeout": "Requests time out after 5 seconds.",
    "memory-exhaustion": "The runtime was killed.",
    "dynamodb-throttle": "DynamoDB reads are throttled.",
    "broken-env-var": "DynamoDB calls fail.",
}


def go(lab: FakeLab, time: FakeTime, name: str, lines: list[str]) -> int:
    return run(
        lab,
        SCENARIOS[name](lab),
        recovery_minutes=6,
        out=lines.append,
        clock=time.clock,
        sleep=time.sleep,
        now=time.now,
    )


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_each_scenario_passes_on_the_right_cause_and_leaves_shoplite_clean(name: str) -> None:
    time, lines = FakeTime(), list[str]()
    lab = FakeLab(time, diagnosis=GOOD[name])
    assert go(lab, time, name, lines) == 0, lines
    assert lab.flags == {}
    assert lab.live == "4"
    assert lab.capacity == 5
    assert lab.env == {"TABLE_NAME": "shoplite"}
    scenario = SCENARIOS[name](lab)
    assert scenario.slug == name
    assert scenario.fix in {
        "rollback-lambda-alias",
        "reset-feature-flags",
        "raise-dynamodb-capacity",
    }


@pytest.mark.parametrize("name", list(SYMPTOM_ONLY))
def test_naming_only_the_symptom_does_not_count(name: str) -> None:
    lab = FakeLab(FakeTime())
    assert mentions(SYMPTOM_ONLY[name], SCENARIOS[name](lab).expect) is None


def test_throttling_sends_load_in_parallel() -> None:
    time, lines = FakeTime(), list[str]()
    lab = FakeLab(time, diagnosis=GOOD["dynamodb-throttle"])
    go(lab, time, "dynamodb-throttle", lines)
    assert lab.requests > 4 * 4 * 60  # 4 at a time, every 0.2 s, for 4 minutes
    assert lab.history[0] == "read=1"
    assert lab.history[-1] == "read=5"


def test_broken_env_var_puts_the_settings_back() -> None:
    time, lines = FakeTime(), list[str]()
    lab = FakeLab(time, diagnosis=GOOD["broken-env-var"])
    go(lab, time, "broken-env-var", lines)
    assert lab.history == [
        "settings {'TABLE_NAME': 'shoplite-catalogue'}",
        "live=4",
        "settings restored",
    ]


def test_waits_for_an_open_incident_to_resolve_first() -> None:
    time, lines = FakeTime(), list[str]()
    lab = FakeLab(time, diagnosis=GOOD["error-flag"])
    lab.open_polls = 3
    assert go(lab, time, "error-flag", lines) == 0
    assert lines[0].startswith("ShopLite still has an open incident")
    stuck = FakeLab(FakeTime())
    stuck.open_polls = 10_000
    stuck_lines = list[str]()
    assert go(stuck, stuck.time, "error-flag", stuck_lines) == 2
    assert stuck.flags == {}  # nothing was injected


def test_phrases_with_plus_need_every_part() -> None:
    assert mentions("Capacity was lowered", ["capacity+lower"]) == "capacity+lower"
    assert mentions("Capacity is fine", ["capacity+lower"]) is None
    assert mentions("anything", []) is None
