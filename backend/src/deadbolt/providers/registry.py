"""Configuration-only provider construction boundary."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from pathlib import Path

from deadbolt.contracts.provider import EntitlementProvider
from deadbolt.errors import ProviderError
from deadbolt.providers.fixtures.salesforce import SalesforceFixtureProvider
from deadbolt.providers.fixtures.workday import WorkdayFixtureProvider

ProviderFactory = Callable[[], EntitlementProvider]

_REAL_MODULES = {
    "aws-iam": ("deadbolt.providers.aws_iam", ("AWSIAMProvider", "AwsIamProvider")),
    "github": ("deadbolt.providers.github", ("GitHubProvider",)),
    "slack": ("deadbolt.providers.slack", ("SlackProvider",)),
    "notion": ("deadbolt.providers.notion", ("NotionProvider",)),
}


def _real_factory(system: str) -> ProviderFactory:
    module_name, class_names = _REAL_MODULES.get(
        system, (f"deadbolt.providers.{system.replace('-', '_')}", ("Provider",))
    )

    def construct() -> EntitlementProvider:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise ProviderError(f"real provider is not installed for {system}") from exc
        for class_name in class_names:
            candidate = getattr(module, class_name, None)
            if callable(candidate):
                provider = candidate()
                if isinstance(provider, EntitlementProvider):
                    return provider
        raise ProviderError(f"real provider module has no compliant provider for {system}")

    return construct


def build_providers(
    config: Mapping[str, str],
    *,
    factories: Mapping[str, ProviderFactory] | None = None,
    fixture_seed_dir: str | Path | None = None,
) -> tuple[EntitlementProvider, ...]:
    """Build providers in stable config-key order, without engine coupling."""
    supplied = factories or {}
    result: list[EntitlementProvider] = []
    for system in sorted(config, key=lambda value: value.encode("utf-8")):
        mode = config[system]
        if system in supplied:
            result.append(supplied[system]())
            continue
        if mode == "fixture" and system == "salesforce":
            path = Path(fixture_seed_dir) / "salesforce.json" if fixture_seed_dir else None
            result.append(SalesforceFixtureProvider(path))
        elif mode == "fixture" and system == "workday":
            path = Path(fixture_seed_dir) / "workday.json" if fixture_seed_dir else None
            result.append(WorkdayFixtureProvider(path))
        elif mode == "real":
            result.append(_real_factory(system)())
        else:
            raise ProviderError(f"unsupported provider mode {mode!r} for {system}")
    return tuple(result)


build_provider_set = build_providers

__all__ = ["ProviderFactory", "build_provider_set", "build_providers"]
