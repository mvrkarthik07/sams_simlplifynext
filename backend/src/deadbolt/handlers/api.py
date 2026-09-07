"""Stable HTTP envelope adapter for the fixture-backed browser API."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Final, cast

from deadbolt.api import DemoApi, dynamo_register_state_store
from deadbolt.auth import AuthenticationError, authorize_event
from deadbolt.connections import ConnectionService, SsmConnectionStore

_JSON_HEADERS: Final[dict[str, str]] = {
    "Content-Type": "application/json; charset=utf-8",
}
_HTTP_BAD_REQUEST: Final[int] = 400
_HTTP_NOT_FOUND: Final[int] = 404
_HTTP_NO_CONTENT: Final[int] = 204
_HTTP_UNAUTHORIZED: Final[int] = 401
_JsonObject = dict[str, object]


@lru_cache(maxsize=1)
def _service() -> DemoApi:
    return DemoApi(
        capture_dir=os.environ.get("DEADBOLT_CAPTURE_DIR"),
        connection_service=ConnectionService(SsmConnectionStore()),
        state_store=dynamo_register_state_store(os.environ.get("DEADBOLT_GRAPH_TABLE_NAME")),
    )


def _envelope(data: object, error: _JsonObject | None) -> _JsonObject:
    return {"data": data, "error": error}


def _error(code: str, message: str) -> _JsonObject:
    return {"code": code, "message": message}


def _response(status: int, data: object = None, error: _JsonObject | None = None) -> _JsonObject:
    return {
        "statusCode": status,
        "headers": _JSON_HEADERS,
        "body": json.dumps(_envelope(data, error), separators=(",", ":"), ensure_ascii=True),
    }


def _request_parts(event: Mapping[str, object]) -> tuple[str, str, Mapping[str, object] | None]:
    request_context = event.get("requestContext")
    http_context = request_context.get("http") if isinstance(request_context, Mapping) else None
    method = http_context.get("method", "GET") if isinstance(http_context, Mapping) else "GET"
    path = event.get("rawPath", "/health")
    if not isinstance(method, str) or not isinstance(path, str):
        raise ValueError("invalid request")

    body_value = event.get("body")
    if body_value is None or body_value == "":
        return method, path, None
    if not isinstance(body_value, str):
        raise ValueError("request body must be an object")
    raw = (
        base64.b64decode(body_value)
        if bool(event.get("isBase64Encoded", False))
        else body_value.encode()
    )
    decoded = json.loads(raw)
    if not isinstance(decoded, Mapping):
        raise ValueError("request body must be an object")
    return method, path, cast(Mapping[str, object], decoded)


def _failure(exc: Exception) -> tuple[int, _JsonObject]:
    if isinstance(exc, AuthenticationError):
        return _HTTP_UNAUTHORIZED, _error("UNAUTHORIZED", "authentication required")
    if isinstance(exc, KeyError):
        return 404, _error("NOT_FOUND", "resource not found")
    if isinstance(exc, json.JSONDecodeError):
        return 400, _error("INVALID_REQUEST", "request body must be valid JSON")
    if isinstance(exc, ValueError):
        return 400, _error("INVALID_REQUEST", "invalid request")
    return 500, _error("INTERNAL_ERROR", "internal server error")


def lambda_handler(event: Mapping[str, object], context: object | None = None) -> _JsonObject:
    """Handle a Lambda Function URL request using a stable data/error envelope."""
    del context
    try:
        subject = authorize_event(event)
        method, path, payload = _request_parts(event)
        if method == "OPTIONS":
            return {"statusCode": _HTTP_NO_CONTENT, "headers": _JSON_HEADERS, "body": ""}
        if path.startswith("/api/connections") and subject is None:
            raise AuthenticationError("authentication required")
        status, value = _service().handle(method, path, payload, subject=subject)
        if status >= _HTTP_BAD_REQUEST:
            if status == _HTTP_NOT_FOUND:
                return _response(status, error=_error("NOT_FOUND", "resource not found"))
            return _response(status, error=_error("INVALID_REQUEST", "invalid request"))
        return _response(status, data=value)
    except (AuthenticationError, KeyError, ValueError, json.JSONDecodeError) as exc:
        status, error = _failure(exc)
        return _response(status, error=error)
    except Exception:
        return _response(500, error=_error("INTERNAL_ERROR", "internal server error"))


__all__ = ["lambda_handler"]
