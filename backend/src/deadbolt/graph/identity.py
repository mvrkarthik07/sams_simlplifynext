"""Deterministic cross-system identity linkage with quarantine for uncertainty."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IdentityLink:
    """The selected worker and the evidence strength used to select it."""

    account_id: str
    worker_id: str | None
    strength: str
    quarantined: bool


def resolve_identity(  # noqa: PLR0912 — four explicit precedence sources are intentional.
    account: Mapping[str, object],
    workers: Mapping[str, Mapping[str, object]],
    *,
    alias_map: Mapping[str, str] | None = None,
) -> IdentityLink:
    """Resolve only by explicit identifiers; names and fuzzy similarity are never used."""
    account_id = str(account.get("account_id", account.get("login", "")))
    candidates: list[tuple[str, str]] = []

    external_id = account.get("scim_external_id")
    if isinstance(external_id, str):
        for worker_id, worker in workers.items():
            if worker.get("scim_external_id") == external_id:
                candidates.append((worker_id, "scim"))
    name_id = account.get("saml_name_id")
    if isinstance(name_id, str):
        for worker_id, worker in workers.items():
            if worker.get("saml_name_id") == name_id:
                candidates.append((worker_id, "saml"))
    email = account.get("verified_email")
    if isinstance(email, str):
        normalized = email.strip().lower()
        for worker_id, worker in workers.items():
            worker_email = worker.get("primary_work_email")
            if isinstance(worker_email, str) and worker_email.strip().lower() == normalized:
                candidates.append((worker_id, "email"))
    account_alias = account.get("alias")
    if isinstance(account_alias, str) and alias_map is not None:
        alias_worker_id: str | None = alias_map.get(account_alias)
        if isinstance(alias_worker_id, str) and alias_worker_id in workers:
            candidates.append((alias_worker_id, "alias"))

    ordered = {"scim": 0, "saml": 1, "email": 2, "alias": 3}
    if not candidates:
        return IdentityLink(account_id, None, "quarantine", True)
    candidates.sort(key=lambda item: ordered[item[1]])
    strongest = candidates[0]
    if any(
        worker_id != strongest[0] and ordered[strength] == ordered[strongest[1]]
        for worker_id, strength in candidates
    ):
        return IdentityLink(account_id, None, "ambiguous", True)
    return IdentityLink(account_id, strongest[0], strongest[1], False)


__all__ = ["IdentityLink", "resolve_identity"]
