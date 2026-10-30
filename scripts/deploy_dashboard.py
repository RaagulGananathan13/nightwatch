"""Upload the built dashboard to its S3 bucket and refresh CloudFront (spec F18).

    python -m scripts.deploy_dashboard --bucket <bucket> --distribution <id> [--profile nightwatch]

The file names under assets/ change whenever their content does, so those can be cached
for a year. Everything else (index.html, the eval results) is cached for 0 seconds and its
CloudFront copy is cleared. Files no longer in the build are removed, except config.json,
which Terraform writes, and the public status page under status/, which NightWatch writes
when an update is published.
"""

from __future__ import annotations

import argparse
import mimetypes
import time
from pathlib import Path
from typing import Any

import boto3

DIST = Path(__file__).resolve().parents[1] / "dashboard" / "dist"
KEEP = {"config.json"}  # written by Terraform, not the build
KEEP_UNDER = "status/"  # the public status page, written by NightWatch (spec F13)
TYPES = {
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}


def plan(dist: Path) -> dict[str, tuple[Path, str, str]]:
    """Each object to upload: key -> (file, content type, cache control)."""
    files: dict[str, tuple[Path, str, str]] = {}
    for path in sorted(p for p in dist.rglob("*") if p.is_file()):
        key = path.relative_to(dist).as_posix()
        kind = (
            TYPES.get(path.suffix)
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        if kind.startswith("text/") or kind == "application/json":
            kind += "; charset=utf-8"
        cache = "public, max-age=31536000, immutable" if key.startswith("assets/") else "no-cache"
        files[key] = (path, kind, cache)
    return files


def deploy(s3: Any, cloudfront: Any, bucket: str, distribution: str, dist: Path) -> list[str]:
    files = plan(dist)
    if "index.html" not in files:
        raise SystemExit(f"{dist} has no index.html: run `npm run build` in dashboard/ first")
    for key, (path, kind, cache) in files.items():
        s3.put_object(
            Bucket=bucket, Key=key, Body=path.read_bytes(), ContentType=kind, CacheControl=cache
        )
    stale = [
        obj["Key"]
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket)
        for obj in page.get("Contents", [])
        if obj["Key"] not in files
        and obj["Key"] not in KEEP
        and not obj["Key"].startswith(KEEP_UNDER)
    ]
    for key in stale:
        s3.delete_object(Bucket=bucket, Key=key)
    fresh = ["/", *sorted(f"/{key}" for key in files if not key.startswith("assets/"))]
    cloudfront.create_invalidation(
        DistributionId=distribution,
        InvalidationBatch={
            "Paths": {"Quantity": len(fresh), "Items": fresh},
            "CallerReference": f"nightwatch-{int(time.time())}",
        },
    )
    return stale


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--dist", type=Path, default=DIST)
    parser.add_argument("--profile", default=None, help="AWS profile (CI uses its OIDC role)")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name="us-east-2")
    stale = deploy(
        session.client("s3"),
        session.client("cloudfront"),
        args.bucket,
        args.distribution,
        args.dist,
    )
    uploaded = len(plan(args.dist))
    print(f"uploaded {uploaded} files, removed {len(stale)}, cache refreshed")  # noqa: T201


if __name__ == "__main__":
    main()
