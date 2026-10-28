"""Build a reproducible Lambda zip from a Python package.

    python -m scripts.build_lambda shoplite --out .build/shoplite-api.zip --version <git sha>

The same sources always give a byte-identical zip: fixed timestamps, sorted entries, fixed
file modes and no compression (zlib output can differ between machines; these packages are
only kilobytes). The version stamp goes into ``<package>/_build.json``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import zipfile
from pathlib import Path

FIXED_TIME = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o644 << 16
SKIPPED_DIRS = {"__pycache__"}
SKIPPED_SUFFIXES = {".pyc", ".pyo", ".md"}
BUILD_FILE = "_build.json"


def package_files(package_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in package_dir.rglob("*")
        if path.is_file()
        and not SKIPPED_DIRS.intersection(path.relative_to(package_dir).parts)
        and path.suffix not in SKIPPED_SUFFIXES
        and path.name != BUILD_FILE
    )


def build(package_dir: Path, out: Path, version: str) -> str:
    """Write the zip and return its SHA-256 in base64 (the form Lambda reports as CodeSha256)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in package_files(package_dir):
            name = f"{package_dir.name}/{path.relative_to(package_dir).as_posix()}"
            _add(archive, name, path.read_bytes())
        stamp = json.dumps({"version": version}, sort_keys=True).encode()
        _add(archive, f"{package_dir.name}/{BUILD_FILE}", stamp)
    return base64.b64encode(hashlib.sha256(out.read_bytes()).digest()).decode()


def _add(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
    info.external_attr = FILE_MODE
    archive.writestr(info, data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", type=Path, help="package folder, e.g. shoplite")
    parser.add_argument("--out", type=Path, required=True, help="zip file to write")
    parser.add_argument("--version", default="dev", help="usually the git commit SHA")
    args = parser.parse_args()
    digest = build(args.package, args.out, args.version)
    print(f"built {args.out} version={args.version} sha256={digest}")  # noqa: T201


if __name__ == "__main__":
    main()
