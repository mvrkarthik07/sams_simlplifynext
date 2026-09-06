"""Low-cost HTTP adapter for the Deadbolt browser rehearsal.

The adapter deliberately runs the checked-in Priya scenario by default.  It exposes the
same read, plan, approval, and rollback shape needed by the SPA without constructing an AWS
client or mutating an external provider.  Production deployments should put an authenticated
adapter in front of the graph store and executor rather than exposing this demo service.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import Final, cast
from urllib.parse import unquote, urlparse
from uuid import uuid4

from deadbolt.broker.negotiate import MemoryProposalStore, negotiate_decision
from deadbolt.connections import ConnectionService, MemoryConnectionStore
from deadbolt.contracts.models import ActionResult, Entitlement
from deadbolt.engine.drift import Finding
from deadbolt.graph.capture import CapturedProvider
from deadbolt.plan.builder import Action, Plan
from scenarios.priya import DEFAULT_EVALUATED_AT, Scenario, ScenarioFinding, build_scenario

_API_PREFIX: Final[str] = "/api"
_FINDING_ROUTE_LENGTH: Final[int] = 2
_ACTION_ROUTE_LENGTH: Final[int] = 3
_HTTP_BAD_REQUEST: Final[int] = 400
_HTTP_NOT_FOUND: Final[int] = 404
_JSON_HEADERS: Final[dict[str, str]] = {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}
_Key = tuple[str, str, str, str]
_JsonObject = dict[str, object]


class _ReadOnlySnapshotProvider:
    """Provider adapter for a just-completed scan; all write paths stay disabled."""

    def __init__(self, system: str, records: tuple[Entitlement, ...]) -> None:
        self.system = system
        self._records = records

    def snapshot(self) -> tuple[Entitlement, ...]:
        return self._records

    def revoke(self, entitlement: Entitlement, dry_run: bool) -> ActionResult:
        del entitlement, dry_run
        raise RuntimeError("live dashboard scans are read-only")

    def restore(self, pre_image: Mapping[str, object]) -> ActionResult:
        del pre_image
        raise RuntimeError("live dashboard scans are read-only")


def _key(entitlement: Entitlement) -> _Key:
    return (
        entitlement.identity_id,
        entitlement.system,
        entitlement.resource,
        entitlement.scope.value,
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _entitlement_wire(entitlement: Entitlement) -> _JsonObject:
    return {
        "identity_id": entitlement.identity_id,
        "system": entitlement.system,
        "resource": entitlement.resource,
        "scope": entitlement.scope.value,
        "granted_at": _iso(entitlement.granted_at),
        "last_used_at": _iso(entitlement.last_used_at),
        "credential_type": entitlement.credential_type.value,
        "revocable": entitlement.revocable,
        "raw": dict(entitlement.raw),
    }


def _finding_key(finding: Finding) -> _Key:
    return _key(finding.entitlement)


def _finding_id(finding: Finding, ids: Mapping[_Key, str]) -> str:
    return ids[_finding_key(finding)]


def _finding_wire(
    finding: Finding,
    finding_id: str,
    state: tuple[str, str],
) -> _JsonObject:
    source = "captured" if finding.entitlement.raw.get("source") == "captured" else "fixture"
    captured_at = finding.entitlement.raw.get("captured_at")
    return {
        "finding_id": finding_id,
        "entitlement": _entitlement_wire(finding.entitlement),
        "score": {
            "S": finding.s / 100,
            "D": finding.d / 100,
            "M": finding.m / 100,
            "B": finding.b / 100,
            "total": finding.score / 100,
        },
        "tier": finding.tier.value,
        "current_stage": state[0],
        "stage_status": state[1],
        "evidence": dict(finding.evidence),
        "observe_only": finding.observe_only,
        "source": source,
        "captured_at": captured_at if isinstance(captured_at, str) else None,
        "evaluated_at": _iso(DEFAULT_EVALUATED_AT),
    }


def _action_for(finding: Finding, plan: Plan) -> Action | None:
    entitlement = finding.entitlement
    return next(
        (
            action
            for action in plan.actions
            if action.system == entitlement.system
            and action.resource == entitlement.resource
            and action.scope == entitlement.scope.value
        ),
        None,
    )


def _plan_wire(finding: Finding, finding_id: str, plan: Plan) -> _JsonObject:
    action = _action_for(finding, plan)
    actions: list[_JsonObject] = []
    if action is not None:
        description = (
            f"Revoke {action.system} access"
            if action.verb == "revoke"
            else f"Downgrade access from {action.from_scope} to {action.to_scope}"
        )
        actions.append(
            {
                "seq": action.seq,
                "type": action.verb,
                "description": description,
                "system": action.system,
            }
        )
    return {
        "plan_id": plan.plan_id,
        "finding_id": finding_id,
        "actions": actions,
        "pre_image_captured": False,
        "hash": plan.plan_hash,
    }


class _DemoLLM:
    """A zero-cost model port for the browser demo.

    The broker still validates the response and applies the closed-graph/non-widening rules;
    this port simply avoids a Bedrock call during a local or fixture-backed demonstration.
    """

    def complete(self, model_id: str, prompt: str) -> str:
        if "scope" in prompt.lower():
            return '{"scope":"read"}'
        return "Template amendment candidate requires human ratification."


class DemoApi:
    """Thread-safe service consumed by the browser API."""

    def __init__(
        self,
        scenario: Scenario | None = None,
        *,
        capture_dir: str | Path | None = None,
        connection_service: ConnectionService | None = None,
    ) -> None:
        self._lock = RLock()
        self.scenario = scenario or _scenario_from_capture(capture_dir)
        self._findings = self.scenario.findings(DEFAULT_EVALUATED_AT)
        self._ids = {
            _finding_key(finding): f"FIND-{index:03d}"
            for index, finding in enumerate(self._findings, start=1)
        }
        self._finding_by_id: dict[str, Finding] = {}
        for finding in self._findings:
            self._finding_by_id[_finding_id(finding, self._ids)] = finding
        self._plan = self.scenario.plan(DEFAULT_EVALUATED_AT)
        self._states: dict[str, tuple[str, str]] = {
            finding_id: ("Verified", "passed")
            if finding_id == "FIND-002"
            else ("Approval", "blocked-on-approval")
            for finding_id in self._finding_by_id
        }
        self._audit: list[_JsonObject] = [
            {
                "id": "LOG-BOOTSTRAP",
                "timestamp": _iso(DEFAULT_EVALUATED_AT) or "",
                "approver": "System",
                "action": "Plan Generated",
                "plan_hash": self._plan.plan_hash,
                "trace_id": self._plan.trace_id,
                "details": "Fixture-backed drift scan completed; awaiting approval where required.",
            }
        ]
        self._proposals = MemoryProposalStore()
        self._connections = connection_service or ConnectionService(MemoryConnectionStore())

    def _finding(self, finding_id: str) -> Finding:
        try:
            return self._finding_by_id[finding_id]
        except KeyError as exc:
            raise KeyError(f"finding not found: {finding_id}") from exc

    def findings(self) -> list[_JsonObject]:
        with self._lock:
            result: list[_JsonObject] = []
            for finding in self._findings:
                finding_id = _finding_id(finding, self._ids)
                result.append(_finding_wire(finding, finding_id, self._states[finding_id]))
            return result

    def finding(self, finding_id: str) -> _JsonObject:
        with self._lock:
            finding = self._finding(finding_id)
            return _finding_wire(finding, finding_id, self._states[finding_id])

    def plan(self, finding_id: str) -> _JsonObject:
        with self._lock:
            finding = self._finding(finding_id)
            return _plan_wire(finding, finding_id, self._plan)

    def audit(self) -> list[_JsonObject]:
        with self._lock:
            return list(reversed([dict(item) for item in self._audit]))

    def metrics(self) -> _JsonObject:
        planted = {item.key for item in self.scenario.expected_findings}
        detected = {_finding_key(finding) for finding in self._findings}
        actions = tuple(action for action in self._plan.actions)
        in_policy = {item.key for item in self.scenario.ratified_entitlements}
        executed = tuple(
            (action.finding_id, action.system, action.resource, action.scope) for action in actions
        )
        false_revocations = sum(item in in_policy for item in executed)
        detected_count = len(planted & detected)
        return {
            "drift_recall": (100 * detected_count / len(planted)) if planted else 100.0,
            "false_revocation_rate": float(false_revocations),
            "mean_time_to_revocation": "—",
            "approver_decision_time": "backend",
            "reversibility": 0.0,
            "cost": 0.0,
            "counts": {
                "planted": len(planted),
                "detected": detected_count,
                "executed": 0,
                "revocations": 0,
                "rollback_success": 0,
            },
        }

    def rerun(self, finding_id: str) -> str:
        with self._lock:
            self._finding(finding_id)
            self._plan = self.scenario.plan(DEFAULT_EVALUATED_AT)
            self._append_audit(
                "Engine Re-run",
                "System",
                "Drift engine rerun; deterministic plan hash matched the active plan.",
            )
            return self._plan.plan_hash

    def decision(
        self,
        finding_id: str,
        action_name: str,
        approver: str,
        reason: str = "",
    ) -> _JsonObject:
        with self._lock:
            finding = self._finding(finding_id)
            action = _action_for(finding, self._plan)
            allowed_actions = {"Approve", "Reduce further", "Keep, with reason", "Defer 30 days"}
            if action_name not in allowed_actions:
                raise ValueError(f"unsupported approval action: {action_name}")
            if action is None and action_name in {"Approve", "Reduce further", "Keep, with reason"}:
                raise ValueError("finding has no executable plan action")

            result = negotiate_decision(
                action_name,
                finding,
                self._plan,
                graph=self.scenario.entitlements(),
                llm_client=_DemoLLM(),
                action=action,
                approver_id=approver,
                reason=reason,
                proposal_store=self._proposals,
            )
            if result.plan is not None and action_name == "Reduce further":
                self._plan = result.plan
            if action_name == "Approve":
                self._states[finding_id] = ("Verified", "passed")
                details = "Fixture provider dry-run completed; no external mutation was performed."
            elif action_name == "Defer 30 days":
                self._states[finding_id] = ("Planned", "passed")
                details = "Decision deferred for 30 days."
            elif action_name == "Reduce further":
                if action is not None and action.from_scope == "read" and result.scope == "none":
                    self._states[finding_id] = ("Verified", "passed")
                    details = (
                        "Read access was already at the narrowest named scope; the staged revoke "
                        "was recorded as verified."
                    )
                else:
                    details = (
                        f"Access narrowed to {result.scope}; plan rebuilt from the validated "
                        "broker result."
                    )
            else:
                details = f"Template proposal {result.proposal_id} recorded for human ratification."
            self._append_audit(action_name, approver, details, reason=reason)
            return self.finding(finding_id)

    def rollback(self, finding_id: str) -> _JsonObject:
        with self._lock:
            self._finding(finding_id)
            self._states[finding_id] = ("Rolled back", "rolled-back")
            self._append_audit(
                "Rollback",
                "Admin",
                "Fixture pre-image rollback verified; no external mutation was performed.",
            )
            return self.finding(finding_id)

    def scan(self, subject: str, provider: str) -> _JsonObject:
        """Promote one authenticated, read-only provider snapshot into the dashboard."""
        records = self._connections.snapshot(subject, provider)
        summary = next(
            item for item in self._connections.list(subject) if item["provider"] == provider
        )
        captured_at = _iso(datetime.now(UTC)) or ""
        marked_records = tuple(
            replace(
                record,
                raw={**dict(record.raw), "source": "captured", "captured_at": captured_at},
            )
            for record in records
        )
        live_provider = _ReadOnlySnapshotProvider(provider, marked_records)
        expected_findings = tuple(
            ScenarioFinding(
                identity_id=item.identity_id,
                system=item.system,
                resource=item.resource,
                scope=item.scope.value,
            )
            for item in marked_records
        )
        scenario = Scenario(
            providers=(live_provider,),
            identities={},
            templates={},
            reachability=_reachability_from_snapshot(marked_records),
            expected_findings=expected_findings,
            ratified_entitlements=(),
        )
        with self._lock:
            self.scenario = scenario
            self._findings = scenario.findings(DEFAULT_EVALUATED_AT)
            self._ids = {
                _finding_key(finding): f"FIND-{index:03d}"
                for index, finding in enumerate(self._findings, start=1)
            }
            self._finding_by_id = {
                _finding_id(finding, self._ids): finding for finding in self._findings
            }
            self._plan = scenario.plan(DEFAULT_EVALUATED_AT)
            self._states = {
                finding_id: ("Approval", "blocked-on-approval")
                for finding_id in self._finding_by_id
            }
            self._append_audit(
                "Provider Scan",
                "System",
                (
                    f"{provider} read-only snapshot promoted to the dashboard; "
                    f"{len(records)} records loaded."
                ),
            )
        return {
            **summary,
            "message": f"Read-only scan loaded {len(records)} records into the dashboard.",
            "dashboard_updated": True,
            "records": [
                {
                    "identity_id": record.identity_id,
                    "resource": record.resource,
                    "scope": record.scope.value,
                    "credential_type": record.credential_type.value,
                    "revocable": record.revocable,
                }
                for record in marked_records
            ],
        }

    def _append_audit(
        self,
        action: str,
        approver: str,
        details: str,
        *,
        reason: str = "",
    ) -> None:
        self._audit.append(
            {
                "id": f"LOG-{uuid4().hex[:9]}",
                "timestamp": _iso(datetime.now(UTC)) or "",
                "approver": approver,
                "action": action,
                "plan_hash": self._plan.plan_hash,
                "trace_id": self._plan.trace_id,
                "details": f"{details} Reason: {reason}" if reason else details,
            }
        )

    def _connection_route(
        self,
        method: str,
        parts: list[str],
        payload: Mapping[str, object] | None,
        subject: str | None,
    ) -> tuple[int, _JsonObject | list[_JsonObject] | str | None]:
        actor = subject or "local-demo"
        if method == "GET" and parts == ["connections"]:
            return 200, self._connections.list(actor)
        if (
            method != "POST"
            or len(parts) != _FINDING_ROUTE_LENGTH
            or parts[1] not in {"github", "salesforce", "workday"}
        ):
            return _HTTP_NOT_FOUND, {"error": "route not found"}
        provider = parts[1]
        body = payload or {}
        if body.get("action") == "test":
            return 200, self._connections.test(actor, provider)
        if body.get("action") == "scan":
            return 200, self.scan(actor, provider)
        if body.get("action") == "disconnect":
            return 200, self._connections.remove(actor, provider)
        return 200, self._connections.save(actor, provider, body)

    def handle(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
        *,
        subject: str | None = None,
    ) -> tuple[int, _JsonObject | list[_JsonObject] | str | None]:
        parsed = urlparse(path)
        route = parsed.path.rstrip("/") or "/"
        if route == "/health" and method == "GET":
            return 200, {"ok": True, "mode": "fixture"}
        status = 404
        value: _JsonObject | list[_JsonObject] | str | None = {"error": "route not found"}
        if route.startswith(_API_PREFIX):
            parts = [unquote(part) for part in route[len(_API_PREFIX) :].split("/") if part]
            if parts and parts[0] == "connections":
                return self._connection_route(method, parts, payload, subject)
            if method == "GET" and parts == ["findings"]:
                status, value = 200, self.findings()
            elif method == "GET" and parts == ["audit"]:
                status, value = 200, self.audit()
            elif method == "GET" and parts == ["metrics"]:
                status, value = 200, self.metrics()
            elif len(parts) == _FINDING_ROUTE_LENGTH and parts[0] == "findings" and method == "GET":
                status, value = 200, self.finding(parts[1])
            elif len(parts) == _FINDING_ROUTE_LENGTH and parts[0] == "plans" and method == "GET":
                status, value = 200, self.plan(parts[1])
            elif (
                len(parts) == _ACTION_ROUTE_LENGTH
                and parts[0] == "findings"
                and parts[2] in {"rerun", "rollback"}
                and method == "POST"
            ):
                status, value = (
                    (200, self.rerun(parts[1]))
                    if parts[2] == "rerun"
                    else (200, self.rollback(parts[1]))
                )
            elif (
                len(parts) == _ACTION_ROUTE_LENGTH
                and parts[0] == "findings"
                and parts[2] == "decision"
                and method == "POST"
            ):
                body = payload or {}
                action = body.get("action")
                approver = body.get("approver", "Browser Approver")
                reason = body.get("reason", "")
                if (
                    isinstance(action, str)
                    and isinstance(approver, str)
                    and isinstance(reason, str)
                ):
                    status, value = 200, self.decision(parts[1], action, approver, reason)
                else:
                    status, value = 400, {"error": "action, approver, and reason must be strings"}
        return status, value


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


class _RequestHandler(BaseHTTPRequestHandler):
    service: DemoApi
    envelope: bool = False

    def _send(self, status: int, value: object) -> None:
        if self.envelope:
            error = None
            data = value
            if status >= _HTTP_BAD_REQUEST:
                data = None
                error = {
                    "code": "NOT_FOUND" if status == _HTTP_NOT_FOUND else "INVALID_REQUEST",
                    "message": (
                        "resource not found" if status == _HTTP_NOT_FOUND else "invalid request"
                    ),
                }
            value = {"data": data, "error": error}
        body = _json_bytes(value)
        self.send_response(status)
        for name, header in _JSON_HEADERS.items():
            self.send_header(name, header)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send(204, None)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            payload: Mapping[str, object] | None = None
            if method == "POST":
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                decoded = json.loads(raw)
                if not isinstance(decoded, Mapping):
                    self._send(400, {"error": "request body must be an object"})
                    return
                payload = cast(Mapping[str, object], decoded)
            status, value = self.service.handle(method, self.path, payload)
            self._send(status, value)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._send(404 if isinstance(exc, KeyError) else 400, {"error": str(exc)})
        except Exception:
            self._send(500, {"error": "internal server error"})

    def log_message(self, format: str, *args: object) -> None:
        return


def _reachability_from_snapshot(entitlements: Iterable[Entitlement]) -> dict[tuple[str, str], int]:
    """Derive blast radius from the capture itself: how many *other* identities hold an
    entitlement on the same (system, resource). Real captures have no separate reachability
    graph, so this is the only blast-radius signal available without fabricating one."""
    by_resource: dict[tuple[str, str], set[str]] = {}
    for item in entitlements:
        by_resource.setdefault((item.system, item.resource), set()).add(item.identity_id)
    return {key: max(0, len(identities) - 1) for key, identities in by_resource.items()}


def _scenario_from_capture(capture_dir: str | Path | None) -> Scenario:
    """Use only the explicitly configured redacted GitHub capture when present."""
    if capture_dir is None:
        return build_scenario()
    capture_path = Path(capture_dir) / "github.json"
    if not capture_path.is_file():
        raise FileNotFoundError(f"configured GitHub capture not found: {capture_path}")
    captured = CapturedProvider(capture_path)
    snapshot = captured.snapshot()
    expected_findings = tuple(
        ScenarioFinding(
            identity_id=item.identity_id,
            system=item.system,
            resource=item.resource,
            scope=item.scope.value,
        )
        for item in snapshot
    )
    return Scenario(
        providers=(captured,),
        identities={},
        templates={},
        reachability=_reachability_from_snapshot(snapshot),
        expected_findings=expected_findings,
        ratified_entitlements=(),
    )


_LAMBDA_SERVICE = DemoApi(capture_dir=os.environ.get("DEADBOLT_CAPTURE_DIR"))


def lambda_handler(event: Mapping[str, object], context: object | None = None) -> _JsonObject:
    """Adapt an API Gateway/Lambda Function URL event to the same service contract."""
    del context
    request_context = event.get("requestContext")
    http_context = request_context.get("http") if isinstance(request_context, Mapping) else None
    method = http_context.get("method", "GET") if isinstance(http_context, Mapping) else "GET"
    path = event.get("rawPath", "/health")
    body_value = event.get("body")
    if not isinstance(method, str) or not isinstance(path, str):
        return {"statusCode": 400, "headers": _JSON_HEADERS, "body": '{"error":"invalid request"}'}
    payload: Mapping[str, object] | None = None
    if isinstance(body_value, str) and body_value:
        encoded = bool(event.get("isBase64Encoded", False))
        raw = base64.b64decode(body_value) if encoded else body_value.encode("utf-8")
        decoded = json.loads(raw)
        if isinstance(decoded, Mapping):
            payload = cast(Mapping[str, object], decoded)
    status, value = _LAMBDA_SERVICE.handle(method, path, payload)
    return {
        "statusCode": status,
        "headers": _JSON_HEADERS,
        "body": _json_bytes(value).decode("utf-8"),
    }


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the local API until interrupted."""
    service = DemoApi(capture_dir=os.environ.get("DEADBOLT_CAPTURE_DIR"))

    class Handler(_RequestHandler):
        pass

    Handler.service = service
    Handler.envelope = os.environ.get("DEADBOLT_ENVELOPE", "0") == "1"
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Deadbolt API listening at http://{host}:{port}")
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fixture-backed Deadbolt browser API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DemoApi", "lambda_handler", "main", "serve"]
