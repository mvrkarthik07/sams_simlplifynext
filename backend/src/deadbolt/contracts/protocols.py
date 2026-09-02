"""Compatibility import for the public provider and clock protocols."""

from deadbolt.contracts.clock import Clock, FixedClock
from deadbolt.contracts.provider import EntitlementProvider

__all__ = ["Clock", "EntitlementProvider", "FixedClock"]
