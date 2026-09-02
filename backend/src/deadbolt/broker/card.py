"""Pure Slack Block Kit approval-card construction."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Final, cast

from deadbolt.engine.drift import Finding
from deadbolt.plan.builder import Action, Plan

_ACTION_IDS: Final[tuple[tuple[str, str, str | None], ...]] = (
    ("approve", "Approve", "primary"),
    ("reduce_further", "Reduce further", "danger"),
    ("keep_with_reason", "Keep, with reason", None),
    ("defer_30_days", "Defer 30 days", None),
)


def _value(source: object, key: str, default: object = "") -> object:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _text(source: object, key: str, default: str = "") -> str:
    value = _value(source, key, default)
    return value if isinstance(value, str) else str(value)


def _finding_fields(finding: Finding | Mapping[str, object]) -> tuple[object, Mapping[str, object]]:
    entitlement = _value(finding, "entitlement")
    evidence = _value(finding, "evidence", {})
    if not isinstance(evidence, Mapping):
        evidence = {}
    return entitlement, evidence


def _action_fields(
    action: Action | Mapping[str, object] | None, plan: Plan | Mapping[str, object]
) -> dict[str, object]:
    if action is None:
        actions = _value(plan, "actions", ())
        candidate = (
            next(iter(cast(Iterable[object], actions)), None)
            if isinstance(actions, Iterable) and not isinstance(actions, Mapping)
            else None
        )
        action = candidate if isinstance(candidate, (Action, Mapping)) else None
    if action is None:
        return {"finding_id": "", "seq": 0, "scope": "", "verb": "", "to_scope": ""}
    return {
        "finding_id": _value(action, "finding_id", ""),
        "seq": _value(action, "seq", 0),
        "scope": _value(action, "scope", ""),
        "verb": _value(action, "verb", ""),
        "to_scope": _value(action, "to_scope", ""),
    }


def _plan_hash(plan: Plan | Mapping[str, object]) -> str:
    value = _value(plan, "plan_hash", "")
    if isinstance(value, str):
        return value
    body = _value(plan, "body", {})
    return str(_value(body, "plan_hash", ""))


def _context_value(
    *,
    action_id: str,
    plan: Plan | Mapping[str, object],
    action: Mapping[str, object],
    task_token: str | None,
) -> str:
    context: dict[str, object] = {
        "action_id": action_id,
        "finding_id": action["finding_id"],
        "plan_hash": _plan_hash(plan),
        "plan_id": _text(_value(plan, "envelope", {}), "plan_id"),
        "trace_id": _text(_value(plan, "envelope", {}), "trace_id"),
        "seq": action["seq"],
    }
    if task_token is not None:
        context["task_token"] = task_token
    return json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_approval_card(
    finding: Finding | Mapping[str, object],
    plan: Plan | Mapping[str, object],
    action: Action | Mapping[str, object] | None = None,
    *,
    task_token: str | None = None,
) -> dict[str, object]:
    """Build a deterministic, single-finding approval card."""
    entitlement, evidence = _finding_fields(finding)
    action_fields = _action_fields(action, plan)
    identity_id = _text(entitlement, "identity_id")
    system = _text(entitlement, "system")
    resource = _text(entitlement, "resource")
    scope = _text(entitlement, "scope")
    tier = _text(finding, "tier")
    if not tier:
        tier = _text(action, "tier") if action is not None else ""
    days_unused = evidence.get("days_unused", "unknown")
    days_text = "never" if days_unused == "never" else f"{days_unused} days"
    template_version = next(
        (
            evidence.get(key)
            for key in (
                "template_version_absent",
                "absent_from_template_version",
                "template_version",
            )
            if evidence.get(key) is not None
        ),
        _value(_value(plan, "body", {}), "template_version", "unknown"),
    )
    blast_count = evidence.get("blast_radius_count", 0)
    proposed = f"{action_fields['verb']} {scope} access to {action_fields['to_scope']}"
    blocks: list[dict[str, object]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "Deadbolt approval"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{identity_id}* · `{system}:{resource}`\n"
                    f"Tier *{tier}* · Proposed: *{proposed}*"
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Last use*\n{days_text} ago"},
                {
                    "type": "mrkdwn",
                    "text": f"*Template*\nAbsent from version {template_version}",
                },
                {"type": "mrkdwn", "text": f"*Blast radius*\n{blast_count} reachable identities"},
                {"type": "mrkdwn", "text": f"*Plan hash*\n`{_plan_hash(plan)}`"},
            ],
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": action_id,
                    "text": {"type": "plain_text", "text": label},
                    **({"style": style} if style is not None else {}),
                    "value": _context_value(
                        action_id=action_id,
                        plan=plan,
                        action=action_fields,
                        task_token=task_token,
                    ),
                }
                for action_id, label, style in _ACTION_IDS
            ],
        },
    ]
    return {"blocks": blocks}


build_card = build_approval_card

__all__ = ["build_approval_card", "build_card"]
