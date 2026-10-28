"""Check that a deployed ShopLite answers, retrying while a new version warms up.

    python -m scripts.smoke_test --url https://<id>.lambda-url.us-east-2.on.aws/

Exits non-zero if /health doesn't return 200 with the expected version.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from botocore.session import Session

from shoplite.canary import signed_request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--expect-version", default="", help="fail unless /health reports this")
    parser.add_argument("--attempts", type=int, default=6)
    args = parser.parse_args()

    credentials = Session().get_credentials()
    if credentials is None:
        sys.exit("no AWS credentials found")
    region = os.environ.get("AWS_REGION", "us-east-2")
    health = args.url.rstrip("/") + "/health"

    for attempt in range(1, args.attempts + 1):
        status, body = signed_request("GET", health, credentials, region)
        version = body.get("version") if isinstance(body, dict) else None
        print(f"attempt {attempt}: HTTP {status} version={version}")  # noqa: T201
        if status == 200 and (not args.expect_version or version == args.expect_version):
            return 0
        time.sleep(5)
    return 1


if __name__ == "__main__":
    sys.exit(main())
