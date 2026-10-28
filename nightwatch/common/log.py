"""Structured logs: one JSON line per event on stdout, which Lambda ships to CloudWatch Logs."""

import json
import sys
from typing import Any


def emit(level: str, msg: str, **fields: Any) -> None:
    record = {"level": level, "msg": msg, "service": "nightwatch", **fields}
    sys.stdout.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")
