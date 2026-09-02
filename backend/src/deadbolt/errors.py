"""Typed exceptions raised by Deadbolt application layers."""


class DeadboltError(Exception):
    """Base class for expected Deadbolt failures."""


class ProviderError(DeadboltError):
    """A provider could not read or mutate its external system."""


class IrreversibleActionError(DeadboltError):
    """An action has no usable pre-image and therefore cannot be executed."""


class PolicyViolationError(DeadboltError):
    """A proposed operation violates a deterministic safety policy."""


class IdempotencyConflict(DeadboltError):
    """A different operation already owns the idempotency key."""


class AuditConfigurationError(DeadboltError):
    """Audit persistence has invalid or incomplete immutable-storage settings."""


class AuditChainError(DeadboltError):
    """An audit chain is malformed or cannot be safely extended."""


__all__ = [
    "AuditChainError",
    "AuditConfigurationError",
    "DeadboltError",
    "IdempotencyConflict",
    "IrreversibleActionError",
    "PolicyViolationError",
    "ProviderError",
]
