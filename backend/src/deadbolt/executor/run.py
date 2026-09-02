"""Fail-closed execution of one immutable plan action."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

import structlog

from deadbolt.audit.writer import AuditWriter
from deadbolt.contracts.models import ActionResult, Entitlement
from deadbolt.contracts.provider import EntitlementProvider
from deadbolt.errors import IrreversibleActionError
from deadbolt.executor.idempotency import IdempotencyLease, IdempotencyStore
from deadbolt.plan.builder import Action, Plan
from deadbolt.plan.canonical import canonical_dumps

_LOG = structlog.get_logger(__name__)


class _S3Body(Protocol):
    def read(self) -> bytes: ...


class _S3Client(Protocol):
    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """The observable outcome of one forward action."""

    ok: bool
    plan_id: str
    plan_hash: str
    seq: int
    action_result: ActionResult | None
    rollback_result: ActionResult | None
    audit_key: str | None
    message: str

    @property
    def result(self) -> ActionResult | None:
        """Compatibility alias for callers interested in the provider result."""
        return self.action_result


def _wire(entitlement: Entitlement) -> dict[str, object]:
    return {
        "identity_id": entitlement.identity_id,
        "system": entitlement.system,
        "resource": entitlement.resource,
        "scope": entitlement.scope,
        "granted_at": entitlement.granted_at,
        "last_used_at": entitlement.last_used_at,
        "credential_type": entitlement.credential_type,
        "revocable": entitlement.revocable,
        "raw": dict(entitlement.raw),
    }


def _failure_result(
    action: Action,
    pre_image: Mapping[str, object] | None,
    message: str,
    *,
    dry_run: bool = False,
) -> ActionResult:
    return ActionResult(
        False,
        action.system,
        action.resource,
        action.scope,
        dry_run,
        dict(pre_image) if pre_image is not None else None,
        message,
        0,
    )


def _log_step(plan: Plan, action: Action, step: str) -> None:
    _LOG.info(
        "executor_step",
        step=step,
        plan_hash=plan.plan_hash,
        seq=action.seq,
        trace_id=plan.trace_id,
    )


def _target(provider: EntitlementProvider, action: Action) -> Entitlement:
    candidates = tuple(
        item
        for item in provider.snapshot()
        if item.system == action.system
        and item.resource == action.resource
        and item.scope.value == action.scope
    )
    if not candidates:
        raise IrreversibleActionError(
            f"no pre-image for {action.system}:{action.resource}:{action.scope}"
        )
    if len(candidates) != 1:
        raise IrreversibleActionError(
            f"ambiguous pre-image for {action.system}:{action.resource}:{action.scope}"
        )
    return candidates[0]


def _put_preimage(s3_client: _S3Client, bucket: str, key: str, target: Entitlement) -> None:
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=canonical_dumps(_wire(target)),
        ContentType="application/json",
    )


def read_preimage(s3_client: object, bucket: str, key: str) -> dict[str, object]:
    """Read and validate one canonical pre-image object from S3."""
    client = cast(_S3Client, s3_client)
    response = client.get_object(Bucket=bucket, Key=key)
    body = response.get("Body")
    if not hasattr(body, "read"):
        raise IrreversibleActionError(f"pre-image object has no readable body: {key}")
    raw = cast(_S3Body, body).read()
    decoded = json.loads(raw)
    if not isinstance(decoded, Mapping):
        raise IrreversibleActionError(f"pre-image object is not an object: {key}")
    return {str(name): value for name, value in decoded.items()}


def _matches_preimage(entitlement: Entitlement, pre_image: Mapping[str, object]) -> bool:
    return canonical_dumps(_wire(entitlement)) == canonical_dumps(dict(pre_image))


def verify_restored(provider: EntitlementProvider, pre_image: Mapping[str, object]) -> bool:
    """Return whether the provider snapshot contains the exact restored state."""
    identity = pre_image.get("identity_id")
    resource = pre_image.get("resource")
    if not isinstance(identity, str) or not isinstance(resource, str):
        return False
    return any(
        _matches_preimage(item, pre_image)
        for item in provider.snapshot()
        if item.identity_id == identity and item.resource == resource
    )


def verify_revoked(provider: EntitlementProvider, target: Entitlement) -> bool:
    """Return whether the target entitlement is absent after a revoke."""
    return not any(
        item.identity_id == target.identity_id
        and item.system == target.system
        and item.resource == target.resource
        and item.scope == target.scope
        for item in provider.snapshot()
    )


class Executor:
    """Execute actions with a durable lock, pre-image, rollback, and audit trail."""

    def __init__(  # noqa: PLR0913 — all external effects are explicit and injectable.
        self,
        provider: EntitlementProvider,
        idempotency: IdempotencyStore,
        s3_client: object,
        preimage_bucket: str,
        *,
        audit_writer: AuditWriter | None = None,
        audit_bucket: str | None = None,
    ) -> None:
        self.provider = provider
        self.idempotency = idempotency
        self._s3 = cast(_S3Client, s3_client)
        self.preimage_bucket = preimage_bucket
        self.audit = audit_writer or AuditWriter(audit_bucket or preimage_bucket, s3_client)

    def _audit(
        self,
        plan: Plan,
        action: Action,
        status: str,
        *,
        rollback_of: str | None = None,
        message: str | None = None,
    ) -> str:
        record: dict[str, object] = {
            "event": "execute",
            "status": status,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "seq": action.seq,
            "trace_id": plan.trace_id,
            "system": action.system,
            "resource": action.resource,
            "scope": action.scope,
            "verb": action.verb,
            "pre_image_key": action.pre_image_key or "",
        }
        if rollback_of is not None:
            record["rollback_of"] = rollback_of
        if message is not None:
            record["message"] = message
        return self.audit.write(record)

    def _complete(
        self,
        lease: IdempotencyLease,
        result: ActionResult,
    ) -> None:
        self.idempotency.complete(lease, result)

    def execute(  # noqa: PLR0911, PLR0915 — ordered safety steps need local control flow.
        self, plan: Plan, action: Action
    ) -> ExecutionResult:
        """Run the six ordered executor steps for one action."""
        _log_step(plan, action, "acquire_lock")
        lease = self.idempotency.acquire(
            plan.plan_id,
            action.seq,
            action.resource,
            action.scope,
            system=action.system,
            plan_hash=plan.plan_hash,
            trace_id=plan.trace_id,
            pre_image_key=action.pre_image_key or "",
        )
        if not lease.acquired:
            prior = lease.result
            return ExecutionResult(
                prior.ok if prior is not None else False,
                plan.plan_id,
                plan.plan_hash,
                action.seq,
                prior,
                None,
                None,
                "duplicate delivery: original result returned",
            )

        target: Entitlement | None = None
        pre_image: Mapping[str, object] | None = None
        applied: ActionResult | None = None
        apply_started = False
        try:
            _log_step(plan, action, "capture_preimage")
            if not action.pre_image_key:
                raise IrreversibleActionError("action has no pre-image key")
            target = _target(self.provider, action)
            pre_image = _wire(target)
            _put_preimage(self._s3, self.preimage_bucket, action.pre_image_key, target)

            _log_step(plan, action, "dry_run")
            dry_run = self.provider.revoke(target, dry_run=True)
            if not dry_run.ok:
                self._complete(lease, dry_run)
                _log_step(plan, action, "audit")
                return ExecutionResult(
                    False,
                    plan.plan_id,
                    plan.plan_hash,
                    action.seq,
                    dry_run,
                    None,
                    self._audit(plan, action, "dry_run_failed", message=dry_run.message),
                    dry_run.message,
                )

            _log_step(plan, action, "apply")
            apply_started = True
            applied = self.provider.revoke(target, dry_run=False)
            if not applied.ok:
                self._complete(lease, applied)
                _log_step(plan, action, "audit")
                return ExecutionResult(
                    False,
                    plan.plan_id,
                    plan.plan_hash,
                    action.seq,
                    applied,
                    None,
                    self._audit(plan, action, "apply_failed", message=applied.message),
                    applied.message,
                )

            _log_step(plan, action, "verify")
            if not verify_revoked(self.provider, target):
                _log_step(plan, action, "automatic_rollback")
                rollback = self.provider.restore(pre_image)
                rollback_ok = rollback.ok and verify_restored(self.provider, pre_image)
                final = _failure_result(
                    action,
                    pre_image,
                    "post-state verification failed; automatic rollback "
                    + ("completed" if rollback_ok else "failed"),
                )
                self._complete(lease, final)
                _log_step(plan, action, "audit")
                audit_key = self._audit(
                    plan,
                    action,
                    "rolled_back" if rollback_ok else "rollback_failed",
                    rollback_of=action.pre_image_key,
                    message=final.message,
                )
                return ExecutionResult(
                    False,
                    plan.plan_id,
                    plan.plan_hash,
                    action.seq,
                    applied,
                    rollback,
                    audit_key,
                    final.message,
                )

            _log_step(plan, action, "audit")
            try:
                audit_key = self._audit(plan, action, "completed", message=applied.message)
            except Exception as exc:
                self._complete(lease, applied)
                return ExecutionResult(
                    False,
                    plan.plan_id,
                    plan.plan_hash,
                    action.seq,
                    applied,
                    None,
                    None,
                    f"action completed but audit failed: {type(exc).__name__}",
                )
            self._complete(lease, applied)
            return ExecutionResult(
                True,
                plan.plan_id,
                plan.plan_hash,
                action.seq,
                applied,
                None,
                audit_key,
                applied.message,
            )
        except IrreversibleActionError:
            self.idempotency.release(lease)
            raise
        except Exception:
            # Before apply, releasing permits a safe retry. Once apply was entered,
            # inspect the live state and complete either the action or its rollback.
            if target is None or not apply_started:
                self.idempotency.release(lease)
                raise
            if verify_revoked(self.provider, target):
                completed = applied or ActionResult(
                    True,
                    target.system,
                    target.resource,
                    target.scope.value,
                    False,
                    pre_image,
                    "provider mutation completed",
                    0,
                )
                self._complete(lease, completed)
                _log_step(plan, action, "audit")
                self._audit(
                    plan,
                    action,
                    "completed_after_provider_error",
                    message=completed.message,
                )
                return ExecutionResult(
                    True,
                    plan.plan_id,
                    plan.plan_hash,
                    action.seq,
                    completed,
                    None,
                    None,
                    "provider error followed by verified completion",
                )
            rollback = self.provider.restore(pre_image or {})
            rollback_ok = rollback.ok and verify_restored(self.provider, pre_image or {})
            final = _failure_result(
                action,
                pre_image,
                "provider error; automatic rollback " + ("completed" if rollback_ok else "failed"),
            )
            self._complete(lease, final)
            _log_step(plan, action, "audit")
            self._audit(
                plan,
                action,
                "rolled_back" if rollback_ok else "rollback_failed",
                rollback_of=action.pre_image_key,
                message=final.message,
            )
            return ExecutionResult(
                False,
                plan.plan_id,
                plan.plan_hash,
                action.seq,
                applied,
                rollback,
                None,
                final.message,
            )


def execute(  # noqa: PLR0913 — the functional API mirrors Executor's injectable effects.
    provider: EntitlementProvider,
    plan: Plan,
    action: Action,
    *,
    idempotency: IdempotencyStore,
    s3_client: object,
    preimage_bucket: str,
    audit_writer: AuditWriter | None = None,
    audit_bucket: str | None = None,
) -> ExecutionResult:
    """Functional entry point for one action."""
    return Executor(
        provider,
        idempotency,
        s3_client,
        preimage_bucket,
        audit_writer=audit_writer,
        audit_bucket=audit_bucket,
    ).execute(plan, action)


__all__ = [
    "ExecutionResult",
    "Executor",
    "execute",
    "read_preimage",
    "verify_restored",
    "verify_revoked",
]
