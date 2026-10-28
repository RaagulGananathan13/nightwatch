"""Which build is running.

The build script writes ``_build.json`` (holding the git SHA) into the package inside the
Lambda zip. Local runs and tests have no such file and report ``dev``.
"""

import json
from pathlib import Path

_BUILD_FILE = Path(__file__).with_name("_build.json")


def _load() -> dict[str, str]:
    try:
        data = json.loads(_BUILD_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return {str(key): str(value) for key, value in data.items()} if isinstance(data, dict) else {}


VERSION = _load().get("version", "dev")
