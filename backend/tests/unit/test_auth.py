from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from deadbolt import auth


def test_auth_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEADBOLT_AUTH_REQUIRED", raising=False)
    assert auth.authorize_event({"headers": {}}) is None


def test_auth_flag_accepts_true_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEADBOLT_AUTH_REQUIRED", "yes")
    assert auth.authentication_required()


def test_required_auth_rejects_missing_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEADBOLT_AUTH_REQUIRED", "1")
    with pytest.raises(auth.AuthenticationError, match="authentication required"):
        auth.authorize_event({"headers": {}})


def test_required_auth_rejects_non_mapping_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEADBOLT_AUTH_REQUIRED", "1")
    with pytest.raises(auth.AuthenticationError, match="authentication required"):
        auth.authorize_event([])


def test_required_auth_rejects_non_mapping_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEADBOLT_AUTH_REQUIRED", "1")
    with pytest.raises(auth.AuthenticationError, match="authentication required"):
        auth.authorize_event({"headers": []})


def test_required_auth_rejects_unconfigured_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEADBOLT_AUTH_REQUIRED", "1")
    monkeypatch.setenv("COGNITO_REGION", "")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "")
    with pytest.raises(auth.AuthenticationError, match="not configured"):
        auth.authorize_event({"headers": {"Authorization": "Bearer token"}})


def test_required_auth_rejects_malformed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEADBOLT_AUTH_REQUIRED", "1")
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "pool")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client")
    with pytest.raises(auth.AuthenticationError, match="invalid authentication token"):
        auth.authorize_event({"headers": {"Authorization": "Bearer not-a-jwt"}})


def test_cognito_id_token_is_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
    issuer = "https://cognito-idp.us-east-1.amazonaws.com/pool"
    monkeypatch.setenv("DEADBOLT_AUTH_REQUIRED", "true")
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "pool")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client")
    auth._jwks.cache_clear()
    monkeypatch.setattr(
        auth,
        "_jwks",
        lambda _url: {"keys": [{"kid": "key-1", **__import__("json").loads(public_jwk)}]},
    )
    token = jwt.encode(
        {
            "sub": "operator-1",
            "token_use": "id",
            "iss": issuer,
            "aud": "client",
            "exp": int(time.time()) + 60,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    assert auth.authorize_event({"headers": {"authorization": f"Bearer {token}"}}) == "operator-1"
