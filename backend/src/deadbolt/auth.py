"""Cognito JWT boundary for the public API and MCP adapters."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Final, cast
from urllib.request import urlopen

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm

from deadbolt.errors import DeadboltError

_BEARER_PREFIX: Final[str] = "bearer "
_JWKS_TIMEOUT_SECONDS: Final[float] = 3.0


class AuthenticationError(DeadboltError):
    """Raised when a request is missing or has an invalid Cognito token."""


def authentication_required() -> bool:
    """Return whether boundary authentication is enabled for this deployment."""
    return os.environ.get("DEADBOLT_AUTH_REQUIRED", "0").lower() in {"1", "true", "yes"}


def _header(headers: object, name: str) -> str | None:
    if not isinstance(headers, dict):
        return None
    expected = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == expected and isinstance(value, str):
            return value
    return None


def _configuration() -> tuple[str, str, str]:
    region = os.environ.get("COGNITO_REGION", "")
    pool_id = os.environ.get("COGNITO_USER_POOL_ID", "")
    client_id = os.environ.get("COGNITO_CLIENT_ID", "")
    if not region or not pool_id or not client_id:
        raise AuthenticationError("authentication is not configured")
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
    return issuer, client_id, f"{issuer}/.well-known/jwks.json"


@lru_cache(maxsize=4)
def _jwks(jwks_url: str) -> dict[str, object]:
    # The URL is derived only from the deployed Cognito region and pool ID.
    with urlopen(jwks_url, timeout=_JWKS_TIMEOUT_SECONDS) as response:  # noqa: S310
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise AuthenticationError("invalid authentication key set")
    return value


def _claims(token: str) -> dict[str, object]:
    issuer, client_id, jwks_url = _configuration()
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise AuthenticationError("invalid authentication token")
        keys = _jwks(jwks_url).get("keys")
        if not isinstance(keys, list):
            raise AuthenticationError("invalid authentication key set")
        key = next(
            (item for item in keys if isinstance(item, dict) and item.get("kid") == kid),
            None,
        )
        if not isinstance(key, dict):
            raise AuthenticationError("invalid authentication token")
        public_key = cast(RSAPublicKey, RSAAlgorithm.from_jwk(json.dumps(key)))
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=client_id,
        )
    except AuthenticationError:
        raise
    except (jwt.PyJWTError, TypeError, ValueError, KeyError) as exc:
        raise AuthenticationError("invalid authentication token") from exc
    if claims.get("token_use") != "id":
        raise AuthenticationError("invalid authentication token")
    return {key: value for key, value in claims.items() if isinstance(key, str)}


def authorize_event(event: object) -> str | None:
    """Validate a Cognito ID token and return its subject when auth is enabled."""
    if not authentication_required():
        return None
    if not isinstance(event, dict):
        raise AuthenticationError("authentication required")
    token = _header(event.get("headers"), "authorization")
    if token is None or not token.lower().startswith(_BEARER_PREFIX):
        raise AuthenticationError("authentication required")
    raw_token = token[len(_BEARER_PREFIX) :].strip()
    if not raw_token:
        raise AuthenticationError("authentication required")
    claims = _claims(raw_token)
    subject = claims.get("sub")
    return subject if isinstance(subject, str) else None


__all__ = ["AuthenticationError", "authentication_required", "authorize_event"]
