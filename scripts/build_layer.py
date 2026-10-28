"""Build the Lambda layer holding NightWatch's third-party packages, for linux arm64.

    python -m scripts.build_layer --out .build/nightwatch-deps.zip

pip fetches the manylinux aarch64 wheels pinned in requirements-lambda.txt, whatever machine
runs this. Files that differ between pip versions (RECORD, INSTALLER, ...) are left out, so the
zip is identical on Windows and in CI. If the requirements haven't changed since the last
build, the existing zip is kept, which saves a slow download.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess  # nosec B404 - runs pip with fixed arguments
import sys
import tempfile
from pathlib import Path

from scripts.build_lambda import write_zip

PLATFORM = "manylinux2014_aarch64"
PYTHON_VERSION = "3.13"
SKIPPED_NAMES = {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}


def install(requirements: Path, target: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--no-deps",  # every dependency is pinned in the requirements file
        "--no-compile",
        "--only-binary=:all:",
        "--platform",
        PLATFORM,
        "--implementation",
        "cp",
        "--python-version",
        PYTHON_VERSION,
        "--target",
        str(target),
        "-r",
        str(requirements),
    ]
    subprocess.run(command, check=True)  # noqa: S603  # nosec B603


def layer_entries(site: Path) -> list[tuple[str, bytes]]:
    """Every installed file under ``python/``, where Lambda looks for layer packages."""
    return [
        (f"python/{path.relative_to(site).as_posix()}", path.read_bytes())
        for path in sorted(site.rglob("*"))
        if path.is_file() and path.name not in SKIPPED_NAMES and "__pycache__" not in path.parts
    ]


def build(requirements: Path, out: Path, force: bool = False) -> str:
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = out.with_name(out.name + ".requirements-sha256")
    if not force and out.exists() and marker.exists() and marker.read_text() == fingerprint:
        return "unchanged"
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "python"
        install(requirements, site)
        digest = write_zip(layer_entries(site), out)
    marker.write_text(fingerprint)
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--requirements", type=Path, default=Path("requirements-lambda.txt"))
    parser.add_argument("--out", type=Path, default=Path(".build/nightwatch-deps.zip"))
    parser.add_argument("--force", action="store_true", help="rebuild even if nothing changed")
    args = parser.parse_args()
    result = build(args.requirements, args.out, args.force)
    print(f"layer {args.out}: {result}")  # noqa: T201


if __name__ == "__main__":
    main()
