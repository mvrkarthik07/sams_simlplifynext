"""The bounded LLM boundary used by approval negotiation.

The model may write prose or suggest a narrower scope.  It never supplies the
decision, and every returned scope is checked against the current graph before
it can be used to construct an action.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Final, Protocol, cast

import boto3  # type: ignore[import-untyped]  # boto3 does not publish strict typing metadata.

from deadbolt.contracts.models import Entitlement
from deadbolt.engine.drift import Finding
from deadbolt.engine.scoring import scope_severity
from deadbolt.errors import PolicyViolationError, ProviderError
from deadbolt.plan.builder import Action, Plan
from deadbolt.plan.canonical import plan_hash
from deadbolt.plan.preimage import preimage_key

NOVA_LITE_MODEL: Final[str] = "amazon.nova-lite-v1:0"
CLAUDE_HAIKU_MODEL: Final[str] = "anthropic.claude-3-haiku-20240307-v1:0"
BEDROCK_REGION: Final[str] = "us-east-1"
NO_ACCESS_SCOPE: Final[str] = "none"


class LLMClient(Protocol):
    """Small testable surface shared by Bedrock and fake model clients."""

    def complete(self, model_id: str, prompt: str) -> str:
        """Return one model response as text."""
        ...


class ProposalStore(Protocol):
    """Persistence port for unratified template-amendment candidates."""

    def write(self, record: Mapping[str, object]) -> str:
        """Persist a proposal and return its identifier."""
        ...


class _BedrockClient(Protocol):
    def converse(self, **kwargs: object) -> Mapping[str, object]: ...


class BedrockLLMClient:
    """Lazy Bedrock Converse adapter, fixed to the PRD's region."""

    def __init__(self, client: object | None = None) -> None:
        supplied = (
            client
            if client is not None
            else boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        )
        self._client = cast(_BedrockClient, supplied)

    def complete(self, model_id: str, prompt: str) -> str:
        response = self._client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 600, "temperature": 0},
        )
        output = response.get("output")
        if not isinstance(output, Mapping):
            raise ProviderError("Bedrock response did not contain an output message")
        message = output.get("message")
        if not isinstance(message, Mapping):
            raise ProviderError("Bedrock response did not contain a message")
        content = message.get("content")
        if not isinstance(content, Iterable) or isinstance(content, (str, bytes, bytearray)):
            raise ProviderError("Bedrock response did not contain message content")
        text_parts = [item.get("text") for item in content if isinstance(item, Mapping)]
        text = "".join(part for part in text_parts if isinstance(part, str))
        if not text:
            raise ProviderError("Bedrock response did not contain text")
        return text


@dataclass(frozen=True, slots=True)
class NegotiationResult:
    """Validated result of one approval action."""

    decision: str
    scope: str | None
    accepted_model_scope: bool
    rejection_reason: str | None
    proposal_id: str | None
    proposal: Mapping[str, object] | None
    plan: Plan | None
    audit_record: Mapping[str, object]


class MemoryProposalStore:
    """Tiny injectable store useful for local Lambda rehearsals and tests."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def write(self, record: Mapping[str, object]) -> str:
        proposal_id = f"proposal-{len(self.records) + 1}"
        detached = dict(record)
        detached["proposal_id"] = proposal_id
        self.records.append(detached)
        return proposal_id


def _object_value(value: object, key: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _text(value: object, key: str, default: str = "") -> str:
    candidate = _object_value(value, key, default)
    if isinstance(candidate, Enum):
        candidate = candidate.value
    return candidate if isinstance(candidate, str) else str(candidate)


def graph_scopes(graph: Iterable[Entitlement | Mapping[str, object] | str]) -> tuple[str, ...]:
    """Return the closed, UTF-8 ordered scope set represented by the graph."""
    if isinstance(graph, Mapping):
        candidate = graph.get("entitlements", graph.get("scopes", ()))
        graph = (
            cast(Iterable[Entitlement | Mapping[str, object] | str], candidate)
            if isinstance(candidate, Iterable)
            and not isinstance(candidate, (str, bytes, bytearray))
            else ()
        )
    found: set[str] = set()
    for item in graph:
        raw = item if isinstance(item, str) else _object_value(item, "scope")
        if isinstance(raw, Enum):
            raw = raw.value
        if isinstance(raw, str):
            try:
                scope_severity(raw)
            except ValueError:
                continue
            found.add(raw)
    return tuple(sorted(found, key=lambda value: value.encode("utf-8")))


def next_lowest_scope(current_scope: str, closed_scopes: Iterable[str]) -> str:
    """Pick the tightest available reduction immediately below ``current_scope``."""
    current_severity = scope_severity(current_scope)
    candidates: list[str] = []
    for scope in set(closed_scopes):
        try:
            severity = scope_severity(scope)
        except ValueError:
            continue
        if scope != current_scope and severity < current_severity:
            candidates.append(scope)
    if not candidates:
        return NO_ACCESS_SCOPE
    return sorted(candidates, key=lambda value: (scope_severity(value), value.encode("utf-8")))[-1]


def _scope_from_response(response: str) -> str:
    try:
        decoded = json.loads(response)
    except json.JSONDecodeError:
        return ""
    if not isinstance(decoded, Mapping):
        return ""
    scope = decoded.get("scope")
    return scope if isinstance(scope, str) else ""


def _finding_id(
    finding: Finding | Mapping[str, object], action: Action | Mapping[str, object] | None
) -> str:
    action_id = _text(action, "finding_id") if action is not None else ""
    return action_id or _text(finding, "finding_id")


def _replan(plan: Plan, action: Action, to_scope: str) -> Plan:
    new_verb = "revoke" if to_scope == NO_ACCESS_SCOPE else "downgrade"
    updated = replace(action, verb=new_verb, to_scope=to_scope, pre_image_key=None)
    body_actions = [
        updated.as_dict() if candidate.seq == action.seq else candidate.as_dict()
        for candidate in plan.actions
    ]
    body = dict(plan.body)
    body["actions"] = body_actions
    digest = plan_hash(body)
    actions = tuple(
        replace(
            candidate,
            pre_image_key=preimage_key(digest, candidate.seq, candidate.resource, candidate.scope),
        )
        for candidate in (
            updated if candidate.seq == action.seq else candidate for candidate in plan.actions
        )
    )
    envelope = dict(plan.envelope)
    attempt = envelope.get("attempt", 0)
    envelope["attempt"] = attempt + 1 if isinstance(attempt, int) else 1
    return Plan(body, envelope, actions)


def _action_from_plan(plan: Plan, action: Action | Mapping[str, object] | None) -> Action:
    if isinstance(action, Action):
        return action
    if action is not None:
        if not isinstance(action, Mapping):
            raise PolicyViolationError("negotiation action must be a plan Action")
        scope = _text(action, "scope")
        score = _object_value(action, "score", 0)
        seq = _object_value(action, "seq", 0)
        if not isinstance(score, int) or not isinstance(seq, int):
            raise PolicyViolationError("negotiation action has invalid numeric fields")
        return Action(
            seq,
            _text(action, "system"),
            _text(action, "resource"),
            scope,
            _text(action, "verb"),
            _text(action, "from_scope", scope),
            _text(action, "to_scope", NO_ACCESS_SCOPE),
            _text(action, "finding_id"),
            score,
            _text(action, "tier"),
            _text(action, "pre_image_key") or None,
        )
    if len(plan.actions) != 1:
        raise PolicyViolationError("negotiation requires one selected plan action")
    return plan.actions[0]


def negotiate_decision(  # noqa: PLR0913 — explicit ports make the model boundary testable.
    decision: str,
    finding: Finding | Mapping[str, object],
    plan: Plan,
    *,
    graph: Iterable[Entitlement | Mapping[str, object] | str] = (),
    llm_client: LLMClient,
    action: Action | Mapping[str, object] | None = None,
    approver_id: str = "",
    reason: str = "",
    proposal_store: ProposalStore | None = None,
) -> NegotiationResult:
    """Handle one button choice while keeping the decision deterministic."""
    selected_action = _action_from_plan(plan, action)
    finding_id = _finding_id(finding, selected_action)
    base_audit: dict[str, object] = {
        "approver_id": approver_id,
        "decision": decision,
        "finding_id": finding_id,
        "plan_hash": plan.plan_hash,
        "trace_id": plan.trace_id,
        "acted": decision == "Approve",
    }
    if decision == "Reduce further":
        closed = graph_scopes(graph)
        fallback = next_lowest_scope(selected_action.from_scope, closed)
        prompt = (
            "Return JSON only with one key, scope. Choose a narrower scope from this closed set: "
            f"{list(closed)!r}. Current scope: {selected_action.from_scope}."
        )
        proposed = _scope_from_response(llm_client.complete(NOVA_LITE_MODEL, prompt))
        accepted = proposed in closed and scope_severity(proposed) < scope_severity(
            selected_action.from_scope
        )
        chosen = proposed if accepted else fallback
        rejection_reason = None if accepted else "model scope rejected; deterministic fallback used"
        replanned = _replan(plan, selected_action, chosen)
        base_audit.update({"acted": True, "result_scope": chosen, "model_scope_accepted": accepted})
        return NegotiationResult(
            decision,
            chosen,
            accepted,
            rejection_reason,
            None,
            None,
            replanned,
            base_audit,
        )
    if decision == "Keep, with reason":
        prompt = (
            "Draft a concise template-amendment candidate for this access finding. "
            "Do not recommend any access grant. Approver reason: " + reason
        )
        draft = llm_client.complete(CLAUDE_HAIKU_MODEL, prompt)
        proposal: dict[str, object] = {
            "proposal_type": "template_amendment_candidate",
            "status": "pending_human_ratification",
            "ratified_template_mutated": False,
            "finding_id": finding_id,
            "identity_id": _text(_object_value(finding, "entitlement"), "identity_id"),
            "current_scope": selected_action.from_scope,
            "reason": reason,
            "draft": draft,
            "plan_hash": plan.plan_hash,
            "trace_id": plan.trace_id,
        }
        store = proposal_store if proposal_store is not None else MemoryProposalStore()
        proposal_id = store.write(proposal)
        base_audit.update({"acted": False, "proposal_id": proposal_id})
        return NegotiationResult(
            decision, None, False, None, proposal_id, proposal, None, base_audit
        )
    acted = decision == "Approve"
    if decision not in {"Approve", "Defer 30 days"}:
        raise PolicyViolationError(f"unsupported approval decision: {decision!r}")
    base_audit["acted"] = acted
    return NegotiationResult(
        decision, None, False, None, None, None, plan if acted else None, base_audit
    )


def negotiate_node(
    state: Mapping[str, object],
    *,
    llm_client: LLMClient,
    proposal_store: ProposalStore | None = None,
) -> dict[str, object]:
    """LangGraph-compatible node: state in, serializable state delta out."""
    finding = state.get("finding")
    plan = state.get("plan")
    if not isinstance(finding, (Finding, Mapping)) or not isinstance(plan, Plan):
        raise PolicyViolationError("negotiation state requires a Finding and Plan")
    result = negotiate_decision(
        _text(state, "decision"),
        finding,
        plan,
        graph=cast(Iterable[Entitlement | Mapping[str, object] | str], state.get("graph", ())),
        llm_client=llm_client,
        action=cast(Action | Mapping[str, object] | None, state.get("action")),
        approver_id=_text(state, "approver_id"),
        reason=_text(state, "reason"),
        proposal_store=proposal_store,
    )
    return {
        "decision": result.decision,
        "selected_scope": result.scope,
        "accepted_model_scope": result.accepted_model_scope,
        "rejection_reason": result.rejection_reason,
        "proposal_id": result.proposal_id,
        "proposal": result.proposal,
        "plan": result.plan,
        "audit_record": dict(result.audit_record),
    }


build_node = negotiate_node
negotiate = negotiate_decision


__all__ = [
    "BEDROCK_REGION",
    "CLAUDE_HAIKU_MODEL",
    "NOVA_LITE_MODEL",
    "BedrockLLMClient",
    "LLMClient",
    "MemoryProposalStore",
    "NegotiationResult",
    "ProposalStore",
    "build_node",
    "graph_scopes",
    "negotiate",
    "negotiate_decision",
    "negotiate_node",
    "next_lowest_scope",
]
