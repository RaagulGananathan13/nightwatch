"""ULIDs: unique ids that sort by creation time (https://github.com/ulid/spec)."""

import os
from collections.abc import Callable
from datetime import datetime

from nightwatch.common.timeutil import utcnow

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(now: datetime | None = None, random_bytes: Callable[[int], bytes] = os.urandom) -> str:
    """26 characters: 48 bits of milliseconds, then 80 random bits, in Crockford base32."""
    millis = int((now or utcnow()).timestamp() * 1000)
    value = (millis << 80) | int.from_bytes(random_bytes(10), "big")
    chars = []
    for _ in range(26):
        value, index = divmod(value, 32)
        chars.append(_CROCKFORD[index])
    return "".join(reversed(chars))
