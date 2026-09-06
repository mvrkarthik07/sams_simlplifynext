"""M10 contract checks for the browser API transport."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator

from deadbolt.handlers.api import lambda_handler

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "schema"
_HTTP_OK = 200
_HTTP_NOT_FOUND = 404


def _call(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    event: dict[str, object] = {
        "version": "2.0",
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }
    response = lambda_handler(event, object())
    status = response.get("statusCode")
    raw_body = response.get("body")
    assert isinstance(status, int)
    assert isinstance(raw_body, str)
    envelope = json.loads(raw_body)
    assert isinstance(envelope, dict)
    return status, cast(dict[str, object], envelope)


def _assert_success(envelope: dict[str, object]) -> object:
    assert envelope.keys() == {"data", "error"}
    assert envelope["error"] is None
    return envelope["data"]


@pytest.mark.m10
def test_all_frontend_operations_use_stable_envelopes_and_contract_schema() -> None:
    entitlement_schema = json.loads((_SCHEMA_DIR / "Entitlement.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(entitlement_schema)

    status, envelope = _call("/api/findings")
    assert status == _HTTP_OK
    findings = _assert_success(envelope)
    assert isinstance(findings, list)
    assert findings
    for finding in findings:
        assert isinstance(finding, dict)
        validator.validate(finding["entitlement"])

    status, envelope = _call("/api/findings/FIND-001")
    assert status == _HTTP_OK
    finding = _assert_success(envelope)
    assert isinstance(finding, dict)

    status, envelope = _call("/api/plans/FIND-001")
    assert status == _HTTP_OK
    plan = _assert_success(envelope)
    assert isinstance(plan, dict)
    assert plan["finding_id"] == "FIND-001"

    status, envelope = _call("/api/audit")
    assert status == _HTTP_OK
    assert isinstance(_assert_success(envelope), list)

    status, envelope = _call("/api/metrics")
    assert status == _HTTP_OK
    assert isinstance(_assert_success(envelope), dict)

    status, envelope = _call(
        "/api/findings/FIND-001/decision",
        method="POST",
        body={"action": "Approve", "approver": "M10", "reason": "contract test"},
    )
    assert status == _HTTP_OK
    assert isinstance(_assert_success(envelope), dict)

    status, envelope = _call("/api/findings/FIND-001/rerun", method="POST")
    assert status == _HTTP_OK
    assert isinstance(_assert_success(envelope), str)

    status, envelope = _call("/api/findings/FIND-001/rollback", method="POST")
    assert status == _HTTP_OK
    assert isinstance(_assert_success(envelope), dict)


@pytest.mark.m10
def test_not_found_is_a_typed_error_envelope() -> None:
    status, envelope = _call("/api/findings/does-not-exist")
    assert status == _HTTP_NOT_FOUND
    assert envelope == {
        "data": None,
        "error": {"code": "NOT_FOUND", "message": "resource not found"},
    }
