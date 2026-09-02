"""Pure derivation of the S3 key used for an action pre-image."""

from __future__ import annotations

import hashlib

from deadbolt.contracts.models import Scope


def _scope_text(scope: Scope | str) -> str:
    return scope.value if isinstance(scope, Scope) else scope


def preimage_key(plan_hash: str, seq: int, resource: str, scope: Scope | str) -> str:
    """Derive a stable, collision-resistant S3 key for one action's pre-image."""
    scope_text = _scope_text(scope)
    identity_digest = hashlib.sha256(f"{resource}|{scope_text}".encode()).hexdigest()[:16]
    return f"preimages/{plan_hash}/{seq}-{identity_digest}.json"


def derive_preimage_key(plan_hash: str, seq: int, resource: str, scope: Scope | str) -> str:
    """Descriptive alias for :func:`preimage_key`."""
    return preimage_key(plan_hash, seq, resource, scope)


__all__ = ["derive_preimage_key", "preimage_key"]
