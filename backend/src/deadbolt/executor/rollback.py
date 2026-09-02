"""Verified restoration of executor pre-images."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from deadbolt.audit.writer import AuditWriter
from deadbolt.contracts.models import ActionResult
from deadbolt.contracts.provider import EntitlementProvider
from deadbolt.errors import IrreversibleActionError
from deadbolt.executor.idempotency import IdempotencyStore, StoredAction
from deadbolt.executor.run import read_preimage, verify_restored

_LOG = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RollbackResult:
    """The verified outcome of one or more rollback actions."""

    ok: bool
    plan_id: str
    seq: int | None
    results: tuple[ActionResult, ...]
    audit_keys: tuple[str, ...]
    message: str

    @property
    def result(self) -> ActionResult | None:
        """Return the single provider result when this is a one-action rollback."""
        return self.results[0] if len(self.results) == 1 else None


class RollbackExecutor:
    """Restore pre-images recorded by a forward executor run."""

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
        self._s3 = s3_client
        self.preimage_bucket = preimage_bucket
        self.audit = audit_writer or AuditWriter(audit_bucket or preimage_bucket, s3_client)

    def _rollback_one(self, action: StoredAction) -> tuple[ActionResult, str]:
        _LOG.info(
            "executor_step",
            step="rollback_read_preimage",
            plan_hash=action.plan_hash,
            seq=action.seq,
            trace_id=action.trace_id,
        )
        if not action.pre_image_key:
            raise IrreversibleActionError(f"no pre-image key for action {action.seq}")
        pre_image = read_preimage(self._s3, self.preimage_bucket, action.pre_image_key)
        _LOG.info(
            "executor_step",
            step="rollback_restore",
            plan_hash=action.plan_hash,
            seq=action.seq,
            trace_id=action.trace_id,
        )
        restored = self.provider.restore(pre_image)
        _LOG.info(
            "executor_step",
            step="rollback_verify",
            plan_hash=action.plan_hash,
            seq=action.seq,
            trace_id=action.trace_id,
        )
        verified = restored.ok and verify_restored(self.provider, pre_image)
        status = "rollback_completed" if verified else "rollback_failed"
        message = "rollback verified" if verified else "rollback verification failed"
        _LOG.info(
            "executor_step",
            step="rollback_audit",
            plan_hash=action.plan_hash,
            seq=action.seq,
            trace_id=action.trace_id,
        )
        audit_key = self.audit.write(
            {
                "event": "rollback",
                "status": status,
                "plan_id": action.plan_id,
                "plan_hash": action.plan_hash,
                "trace_id": action.trace_id,
                "system": action.system,
                "resource": action.resource,
                "scope": action.scope,
                "pre_image_key": action.pre_image_key,
                "rollback_of": action.lock_key,
            }
        )
        if not verified:
            return (
                ActionResult(
                    False,
                    action.system,
                    action.resource,
                    action.scope,
                    False,
                    pre_image,
                    message,
                    restored.provider_latency_ms,
                ),
                audit_key,
            )
        return restored, audit_key

    def rollback(self, plan_id: str, seq: int | str = "all") -> RollbackResult:
        """Restore one sequence or every recorded action in sequence order."""
        if seq == "all":
            actions = self.idempotency.get_actions(plan_id)
            selected_seq: int | None = None
        elif isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0:
            action = self.idempotency.get_action(plan_id, seq)
            if action is None:
                raise IrreversibleActionError(f"no recorded action for {plan_id}:{seq}")
            actions = (action,)
            selected_seq = seq
        else:
            raise ValueError("seq must be a non-negative integer or 'all'")

        results: list[ActionResult] = []
        audit_keys: list[str] = []
        failures: list[str] = []
        for action in actions:
            try:
                result, audit_key = self._rollback_one(action)
            except Exception as exc:
                failures.append(f"seq {action.seq}: {type(exc).__name__}")
                continue
            results.append(result)
            audit_keys.append(audit_key)
            if not result.ok:
                failures.append(f"seq {action.seq}: rollback verification failed")
        ok = bool(results) and not failures and all(result.ok for result in results)
        if not actions:
            return RollbackResult(False, plan_id, selected_seq, (), (), "no recorded actions")
        message = "rollback verified" if ok else "; ".join(failures) or "rollback failed"
        return RollbackResult(ok, plan_id, selected_seq, tuple(results), tuple(audit_keys), message)


def rollback(  # noqa: PLR0913 — the functional API mirrors RollbackExecutor's dependencies.
    provider: EntitlementProvider,
    plan_id: str,
    seq: int | str = "all",
    *,
    idempotency: IdempotencyStore,
    s3_client: object,
    preimage_bucket: str,
    audit_writer: AuditWriter | None = None,
    audit_bucket: str | None = None,
) -> RollbackResult:
    """Functional entry point for verified rollback."""
    return RollbackExecutor(
        provider,
        idempotency,
        s3_client,
        preimage_bucket,
        audit_writer=audit_writer,
        audit_bucket=audit_bucket,
    ).rollback(plan_id, seq)


__all__ = ["RollbackExecutor", "RollbackResult", "rollback"]
