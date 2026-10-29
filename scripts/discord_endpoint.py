"""Point the NightWatch Discord application's button clicks at the interactions Lambda.

    python -m scripts.discord_endpoint --url https://<id>.lambda-url.us-east-2.on.aws/

(``make discord-endpoint`` passes the URL from ``terraform output``.) Discord checks the URL
before saving it: it sends a signed PING, which must get a PONG, and a badly signed request,
which must get 401. So run this after ``make apply``, once the Lambda is live.

The bot token is read from SSM and only sent to Discord, never printed.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import boto3

from nightwatch.chat.discord import API, USER_AGENT

_LAMBDA_URL = re.compile(r"^https://[a-z0-9]+\.lambda-url\.[a-z0-9-]+\.on\.aws/?$")


def set_endpoint(
    token: str, url: str, opener: Callable[..., Any] = urllib.request.urlopen
) -> dict[str, Any]:
    """PATCH /applications/@me. Returns the application's id and its new endpoint."""
    if not _LAMBDA_URL.match(url):
        raise ValueError("expected a Lambda function URL")
    request = urllib.request.Request(  # noqa: S310 - fixed https host
        f"{API}/applications/@me",
        data=json.dumps({"interactions_endpoint_url": url}).encode(),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="PATCH",
    )
    try:
        with opener(request, timeout=15) as response:
            body: dict[str, Any] = json.loads(response.read())
    except urllib.error.HTTPError as exc:  # Discord explains why it refused the URL
        raise SystemExit(f"Discord refused (HTTP {exc.code}): {exc.read()[:500]!r}") from None
    return {
        "id": body.get("id"),
        "interactions_endpoint_url": body.get("interactions_endpoint_url"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="the interactions Lambda's function URL")
    parser.add_argument("--token-param", default="/nightwatch/discord/bot_token")
    parser.add_argument("--profile", default="nightwatch")
    args = parser.parse_args()
    ssm = boto3.Session(profile_name=args.profile).client("ssm")
    token = ssm.get_parameter(Name=args.token_param, WithDecryption=True)["Parameter"]["Value"]
    print(json.dumps(set_endpoint(token, args.url.strip()), indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
