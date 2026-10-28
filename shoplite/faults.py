"""Failure injection driven by feature flags.

Each fault imitates something that really happens in production, so NightWatch has to find the
cause from the same evidence a human would: logs, metrics and the record of what changed.
"""

import random
import time
from collections.abc import Callable

from shoplite.flags import Flags

_ONE_MB = 1024 * 1024


class InjectedFaultError(RuntimeError):
    """Raised on purpose by the chaos lab."""


def inject(
    flags: Flags,
    remaining_ms: Callable[[], int],
    sleep: Callable[[float], None] = time.sleep,
    chance: Callable[[], float] = random.random,
) -> list[bytes]:
    """Apply the active faults. Returns memory the caller must hold until the request ends."""
    if flags.extra_latency_ms:
        sleep(flags.extra_latency_ms / 1000)

    hog: list[bytes] = []
    if flags.memory_hog_mb:
        # Non-zero bytes, so the pages are really used (zeroed memory may never be committed).
        hog = [b"x" * _ONE_MB for _ in range(flags.memory_hog_mb)]

    if flags.hang:
        sleep(remaining_ms() / 1000 + 1)

    if flags.error_rate and chance() < flags.error_rate:
        raise InjectedFaultError(f"injected fault (error_rate={flags.error_rate})")
    return hog
