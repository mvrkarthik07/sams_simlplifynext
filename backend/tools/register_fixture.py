"""Explicit local HTTP fixture for register design/interaction reviews, never a live provider.

Run: python tools/register_fixture.py --port 8017
Then: VITE_API_BASE=http://127.0.0.1:8017/api npm run dev (in frontend).
The PRD's twenty-finding scoring scenario is intentionally separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock
from urllib.parse import unquote, urlparse

# Intentionally varied review examples, not evidence from a customer capture.
RECORDS = [
    ("alex.chen", "platform/production", "admin", 92, "Verified", "failed"),
    ("jordan.lee", "platform/identity", "admin", 88, "Approval", "blocked-on-approval"),
    ("priya.shah", "platform/billing", "admin", 78, "Approval", "blocked-on-approval"),
    ("sam.rivera", "platform/api", "write", 72, "Approval", "blocked-on-approval"),
    ("morgan.wu", "platform/deploy", "write", 64, "Executing", "running"),
    ("taylor.kim", "platform/design-system", "write", 58, "Verified", "passed"),
    ("sam.rivera", "platform/docs", "read", 49, "Approval", "blocked-on-approval"),
    ("casey.patel", "platform/status", "write", 42, "Planned", "queued"),
    ("priya.shah", "platform/analytics", "read", 34, "Verified", "passed"),
    ("riley.park", "platform/sandbox", "read", 27, "Detected", "queued"),
    ("sam.rivera", "github:pat:sam.rivera", "read", 18, "Scored", "passed"),
    ("devon.reed", "platform/archive", "read", 12, "Detected", "queued"),
]


def make_findings(count: int) -> list[dict[str, object]]:
    now = datetime.now(UTC).replace(microsecond=0)
    rows = []
    for index in range(count):
        identity, resource, scope, score, stage, status = RECORDS[index % len(RECORDS)]
        if index >= len(RECORDS):
            identity = f"{identity}.{index // len(RECORDS)}"
        captured = (now - timedelta(minutes=35 if index % len(RECORDS) == 4 else 4)).isoformat().replace('+00:00', 'Z')
        rows.append({
            "finding_id": f"FIND-{index + 1:03d}",
            "entitlement": {"identity_id": identity, "system": "github", "resource": resource,
                "scope": scope, "granted_at": None, "last_used_at": None,
                "credential_type": "pat" if ':pat:' in resource else "federated",
                "revocable": ':pat:' not in resource,
                "raw": {"source": "fixture", "unavailable": index % len(RECORDS) == 11}},
            "score": {"S": score, "D": score, "M": score, "B": score, "total": score},
            "tier": 'T0' if score < 30 else 'T1' if score < 60 else 'T2' if score < 85 else 'T3',
            "current_stage": stage, "stage_status": status,
            "evidence": {"days_unused": 147 - index % 12 * 11, "role_mismatch": index % 3 != 2, "blast_radius_count": max(1, 31 - index % 12 * 3)},
            "observe_only": ':pat:' in resource, "source": "fixture", "captured_at": captured,
            "evaluated_at": captured,
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8017)
    parser.add_argument('--rows', type=int, default=12)
    args = parser.parse_args()
    findings = make_findings(args.rows)
    lock = RLock()
    audit: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return  # Local review server deliberately does not log request payloads.

        def reply(self, data: object, status: int = 200, message: str | None = None) -> None:
            body = json.dumps({"data": data, "error": {"code": "FIXTURE_ERROR", "message": message} if message else None}).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.reply(None)

        def do_GET(self) -> None:
            path = unquote(urlparse(self.path).path).split('/')
            with lock:
                if path[-1] == 'findings':
                    self.reply(findings)
                elif path[-1] == 'metrics':
                    self.reply({"drift_recall": 85.7, "false_revocation_rate": 0,
                        "mean_time_to_revocation": "42s", "approver_decision_time": "—",
                        "reversibility": 80, "cost": 0,
                        "counts": {"planted": 14, "detected": 12, "executed": 5, "rollback_success": 4, "revocations": 5}})
                elif path[-1] in ('audit', 'connections'):
                    self.reply(audit if path[-1] == 'audit' else [])
                elif len(path) == 4 and path[2] in ('findings', 'plans'):
                    finding = next((row for row in findings if row['finding_id'] == path[3]), None)
                    if finding is None:
                        self.reply(None, 404, 'Finding no longer exists. Refresh the queue.')
                    elif path[2] == 'findings':
                        self.reply(finding)
                    else:
                        body = json.dumps(finding, sort_keys=True, separators=(',', ':')).encode()
                        self.reply({"plan_id": f"PLAN-{path[3]}-REVIEW", "finding_id": path[3],
                            "actions": [] if finding['tier'] == 'T0' else [{"seq": 1, "type": "downgrade", "description": "Downgrade access to read", "system": "github"}],
                            "pre_image_captured": finding['stage_status'] == 'passed', "hash": hashlib.sha256(body).hexdigest()})
                else:
                    self.reply(None, 404, 'This route is not part of the review fixture.')

        def do_POST(self) -> None:
            path = unquote(urlparse(self.path).path).split('/')
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or '{}')
            with lock:
                finding = next((row for row in findings if len(path) > 3 and row['finding_id'] == path[3]), None)
                if finding is None:
                    self.reply(None, 404, 'Finding no longer exists. Refresh the queue.')
                    return
                if path[-1] == 'decision' and data.get('action') in ('Approve', 'Reduce further', 'Defer 30 days', 'Keep, with reason'):
                    finding['current_stage'] = 'Planned' if data['action'] in ('Defer 30 days', 'Keep, with reason') else 'Verified'
                    finding['stage_status'] = 'passed'
                elif path[-1] == 'rollback':
                    finding['current_stage'], finding['stage_status'] = 'Rolled back', 'rolled-back'
                else:
                    self.reply(None, 400, 'Choose a supported review decision.')
                    return
                audit.append({"id": f"REVIEW-{len(audit) + 1}", "timestamp": datetime.now(UTC).isoformat(), "approver": 'Access Reviewer', "action": data.get('action', 'Restore access'), "plan_hash": 'fixture', "trace_id": 'fixture', "details": 'Local fixture decision; no provider write.'})
                self.reply(finding)

    print(f'Register fixture: http://127.0.0.1:{args.port}/api ({args.rows} findings)', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
