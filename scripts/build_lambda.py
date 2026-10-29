"""Build a reproducible Lambda zip from a Python package.

    python -m scripts.build_lambda shoplite --out .build/shoplite-api.zip --version <git sha>
    python -m scripts.build_lambda nightwatch --out .build/nightwatch.zip --extra runbooks:nightwatch/runbooks

The same sources always give a byte-identical zip: fixed timestamps, sorted entries, fixed
file modes and no compression (zlib output can differ between machines; these packages are
only kilobytes). ``--version`` writes a stamp into ``<package>/_build.json``. Leave it out for
code Terraform deploys, or every commit would look like a code change. ``--extra SRC:DEST``
adds a folder from outside the package, e.g. the repo's runbooks.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import zipfile
from collections.abc import Iterable
from pathlib import Path

FIXED_TIME = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o644 << 16
SKIPPED_DIRS = {"__pycache__"}
SKIPPED_SUFFIXES = {".pyc", ".pyo"}
SKIPPED_NAMES = {"README.md", "_build.json"}
BUILD_FILE = "_build.json"


def package_files(package_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in package_dir.rglob("*")
        if path.is_file()
        and not SKIPPED_DIRS.intersection(path.relative_to(package_dir).parts)
        and path.suffix not in SKIPPED_SUFFIXES
        and path.name not in SKIPPED_NAMES
    )


def build(
    package_dir: Path,
    out: Path,
    version: str | None = None,
    extras: Iterable[tuple[Path, str]] = (),
) -> str:
    """Zip the package (and the version stamp, if given). Returns the zip's SHA-256."""
    folders = [(package_dir, package_dir.name), *extras]
    entries = [
        (f"{prefix}/{path.relative_to(folder).as_posix()}", path.read_bytes())
        for folder, prefix in folders
        for path in package_files(folder)
    ]
    if version is not None:
        stamp = json.dumps({"version": version}, sort_keys=True).encode()
        entries.append((f"{package_dir.name}/{BUILD_FILE}", stamp))
    return write_zip(entries, out)


def write_zip(entries: Iterable[tuple[str, bytes]], out: Path) -> str:
    """Write a deterministic zip. Returns its SHA-256 in base64, the form Lambda calls CodeSha256."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.external_attr = FILE_MODE
            archive.writestr(info, data)
    return base64.b64encode(hashlib.sha256(out.read_bytes()).digest()).decode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", type=Path, help="package folder, e.g. shoplite")
    parser.add_argument("--out", type=Path, required=True, help="zip file to write")
    parser.add_argument("--version", default=None, help="stamp, usually the git commit SHA")
    parser.add_argument(
        "--extra", action="append", default=[], help="SRC:DEST folder to add, e.g. runbooks:x/y"
    )
    args = parser.parse_args()
    extras = [(Path(src), dest.strip("/")) for src, dest in (e.split(":", 1) for e in args.extra)]
    digest = build(args.package, args.out, args.version, extras)
    print(f"built {args.out} version={args.version or '-'} sha256={digest}")  # noqa: T201


if __name__ == "__main__":
    main()
