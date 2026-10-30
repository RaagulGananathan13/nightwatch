"""Checks the dashboard's Cognito access token on every API call (spec F18).

CloudFront signs its requests to this function (origin access control), so the Authorization
header belongs to AWS. The browser sends its Cognito access token in ``X-NightWatch-Token``
instead. A token counts only if all of these hold:
- Cognito signed it, with RS256 and one of the user pool's published keys;
- it hasn't expired (30 s of clock leeway);
- it is an access token, not an ID token;
- it was issued to the dashboard's own app client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt


class AuthError(Exception):
    """No token, or one that doesn't pass every check."""


@dataclass(frozen=True)
class User:
    username: str
    groups: tuple[str, ...]


class Verifier:
    def __init__(self, region: str, pool_id: str, client_id: str, keys: Any | None = None) -> None:
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
        self.client_id = client_id
        # The pool's public keys, fetched once and cached for the life of the container.
        self._keys = keys or jwt.PyJWKClient(
            f"{self.issuer}/.well-known/jwks.json", cache_keys=True, timeout=5
        )

    def verify(self, token: str) -> User:
        if not token:
            raise AuthError("sign in first")
        try:
            key = self._keys.get_signing_key_from_jwt(token).key
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.issuer,
                leeway=30,
                options={"require": ["exp", "iat", "iss", "token_use", "client_id"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token ({type(exc).__name__})") from None
        access = claims["token_use"] == "access"  # noqa: S105  # nosec B105 - a claim, not a secret
        if not access or claims["client_id"] != self.client_id:
            raise AuthError("not a token for this dashboard")
        return User(
            username=str(claims.get("username", "")),
            groups=tuple(claims.get("cognito:groups", [])),
        )
