"""Structured logging: exactly one JSON line per event.

Lambda sends stdout to CloudWatch Logs, and Logs Insights discovers JSON fields by itself, so
NightWatch can run ``stats count() by error_type`` without parsing free text.
"""

import json
import sys
from typing import Any

from shoplite.build_info import VERSION

SERVICE = "shoplite"


def emit(level: str, msg: str, **fields: Any) -> None:
    """Write one log line. ``version`` is the git SHA, so errors can be tied to a deploy."""
    record = {"level": level, "msg": msg, "service": SERVICE, "version": VERSION, **fields}
    sys.stdout.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")
