"""Slack Function URL handler for approval callbacks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping
from typing import Protocol, cast
from urllib.parse import parse_qs

import boto3  # type: ignore[import-untyped]  # boto3 does not publish strict typing metadata.

from deadbolt.audit.writer import AuditWriter
from deadbolt.errors import PolicyViolationError

MAX_SIGNATURE_AGE_SECONDS = 300
_ACTION_IDS = {
    "approve": "Approve",
    "reduce_further": "Reduce further",
    "keep_with_reason": "Keep, with reason",
    "defer_30_days": "Defer 30 days",
}


class _StepFunctionsClient(Protocol):
    def send_task_success(self, **kwargs: object) -> Mapping[str, object]: ...


class _AuditWriter(Protocol):
    def write(self, record: Mapping[str, object]) -> str: ...


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: str,
    signature: str,
    *,
    now: float | None = None,
) -> bool:
    """Verify Slack's v0 HMAC and reject replayed or malformed timestamps."""
    try:
        timestamp_value = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - timestamp_value) > MAX_SIGNATURE_AGE_SECONDS:
        return False
    base = f"v0:{timestamp}:{body}".encode()
    expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


is_valid_slack_signature = verify_slack_signature
verify_signature = verify_slack_signature


def _headers(event: Mapping[str, object]) -> dict[str, str]:
    raw = event.get("headers", {})
    if not isinstance(raw, Mapping):
        return {}
    return {str(key).lower(): str(value) for key, value in raw.items()}


def _raw_body(event: Mapping[str, object]) -> str:
    body = event.get("body", "")
    text = body if isinstance(body, str) else str(body)
    if bool(event.get("isBase64Encoded", False)):
        return base64.b64decode(text).decode("utf-8")
    return text


def _payload(body: str) -> Mapping[str, object]:
    if body.startswith("payload="):
        values = parse_qs(body, keep_blank_values=True)
        raw = values.get("payload", [""])[0]
        decoded = json.loads(raw)
    else:
        decoded = json.loads(body)
    if not isinstance(decoded, Mapping):
        raise PolicyViolationError("Slack payload must be a JSON object")
    return decoded


def _first_action(payload: Mapping[str, object]) -> Mapping[str, object]:
    actions = payload.get("actions", [])
    if not isinstance(actions, list) or not actions or not isinstance(actions[0], Mapping):
        raise PolicyViolationError("Slack payload has no action")
    return actions[0]


def _context(action: Mapping[str, object], payload: Mapping[str, object]) -> dict[str, object]:
    raw_value = action.get("value", payload.get("value", "{}"))
    if isinstance(raw_value, str):
        try:
            decoded = json.loads(raw_value)
        except json.JSONDecodeError:
            decoded = {}
    else:
        decoded = raw_value
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _approver_id(payload: Mapping[str, object]) -> str:
    user = payload.get("user", {})
    if isinstance(user, Mapping):
        for key in ("id", "username", "name"):
            value = user.get(key)
            if isinstance(value, str) and value:
                return value
    return "unknown"


def _text(source: Mapping[str, object], key: str, default: str = "") -> str:
    value = source.get(key, default)
    return value if isinstance(value, str) else str(value)


def _response(status_code: int, body: Mapping[str, object]) -> dict[str, object]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(dict(body), sort_keys=True, separators=(",", ":")),
    }


def lambda_handler(  # noqa: PLR0913 — Lambda adapter dependencies stay injectable for tests.
    event: Mapping[str, object],
    context: object | None = None,
    *,
    stepfunctions_client: object | None = None,
    audit_writer: _AuditWriter | None = None,
    signing_secret: str | None = None,
    now: float | None = None,
) -> dict[str, object]:
    """Handle Slack interactivity without trusting any client-supplied decision."""
    del context
    body = _raw_body(event)
    headers = _headers(event)
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    secret = (
        signing_secret if signing_secret is not None else os.environ.get("SLACK_SIGNING_SECRET", "")
    )
    if not verify_slack_signature(secret, timestamp, body, signature, now=now):
        return _response(401, {"error": "invalid Slack signature"})
    try:
        payload = _payload(body)
        action = _first_action(payload)
        action_name = _text(action, "action_id")
        decision = _ACTION_IDS.get(action_name)
        if decision is None:
            raise PolicyViolationError("unsupported Slack action")
        details = _context(action, payload)
        task_token = _text(details, "task_token") or _text(payload, "task_token")
        if not task_token:
            raise PolicyViolationError("Slack action has no Step Functions task token")
        approver_id = _approver_id(payload)
        reason = _text(payload, "reason") or _text(details, "reason")
        record: dict[str, object] = {
            "approver_id": approver_id,
            "decision": decision,
            "finding_id": _text(details, "finding_id"),
            "plan_hash": _text(details, "plan_hash"),
            "trace_id": _text(details, "trace_id"),
            "acted": decision == "Approve",
            "reason": reason,
        }
        writer = audit_writer
        if writer is None:
            bucket = os.environ.get("AUDIT_BUCKET", "")
            if bucket:
                writer = AuditWriter(bucket, boto3.client("s3", region_name="us-east-1"))
        if writer is not None:
            writer.write(record)
        client = (
            cast(_StepFunctionsClient, stepfunctions_client)
            if stepfunctions_client is not None
            else cast(_StepFunctionsClient, boto3.client("stepfunctions", region_name="us-east-1"))
        )
        output = json.dumps(
            {"decision": decision, "approver_id": approver_id, "reason": reason},
            sort_keys=True,
            separators=(",", ":"),
        )
        client.send_task_success(taskToken=task_token, output=output)
        return _response(200, {"ok": True})
    except (ValueError, PolicyViolationError) as exc:
        return _response(400, {"error": str(exc)})


handler = lambda_handler
handle_slack_interaction = lambda_handler

__all__ = [
    "MAX_SIGNATURE_AGE_SECONDS",
    "handle_slack_interaction",
    "handler",
    "is_valid_slack_signature",
    "lambda_handler",
    "verify_signature",
    "verify_slack_signature",
]
