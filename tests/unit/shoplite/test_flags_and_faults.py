"""Feature flags parse defensively and each fault does what the chaos lab expects."""

import pytest

from shoplite import faults
from shoplite.flags import Flags, FlagSource


@pytest.mark.parametrize("text", ["", "not json", "[]", "null", '"on"'])
def test_unreadable_flags_mean_everything_off(text: str) -> None:
    assert Flags.from_json(text) == Flags()


def test_flag_values_are_clamped_and_typed() -> None:
    flags = Flags.from_json(
        '{"error_rate": 7, "extra_latency_ms": -5, "memory_hog_mb": "lots", "hang": "yes", "x": 1}'
    )
    assert flags == Flags(error_rate=1.0, extra_latency_ms=0, memory_hog_mb=0, hang=False)
    assert Flags.from_json('{"hang": true, "extra_latency_ms": 1500}') == Flags(
        extra_latency_ms=1500, hang=True
    )


def test_flag_source_caches_and_survives_ssm_outages() -> None:
    now = [0.0]
    source = FlagSource("/shoplite/flags", ttl=10, clock=lambda: now[0])
    # No mock_aws here, so any real call would fail; seed the cache the way a fetch would.
    source._cached, source._fetched_at = Flags(error_rate=0.5), 0.0
    now[0] = 5.0
    assert source.current().error_rate == 0.5  # still fresh, no SSM call

    class BrokenSSM:
        def get_parameter(self, **_: object) -> object:
            from botocore.exceptions import EndpointConnectionError

            raise EndpointConnectionError(endpoint_url="https://ssm")

    source._client = BrokenSSM()  # type: ignore[assignment]
    now[0] = 60.0
    assert source.current().error_rate == 0.5  # stale, SSM down: keep the last known flags


def test_latency_and_hang_faults_sleep() -> None:
    slept: list[float] = []
    faults.inject(Flags(extra_latency_ms=1500), remaining_ms=lambda: 0, sleep=slept.append)
    faults.inject(Flags(hang=True), remaining_ms=lambda: 4000, sleep=slept.append)
    assert slept == [1.5, 5.0]


def test_error_rate_uses_the_dice() -> None:
    flags = Flags(error_rate=0.3)
    faults.inject(flags, remaining_ms=lambda: 0, chance=lambda: 0.9)  # no error
    with pytest.raises(faults.InjectedFaultError):
        faults.inject(flags, remaining_ms=lambda: 0, chance=lambda: 0.1)


def test_memory_hog_really_allocates() -> None:
    hog = faults.inject(Flags(memory_hog_mb=3), remaining_ms=lambda: 0)
    assert sum(len(block) for block in hog) == 3 * 1024 * 1024
