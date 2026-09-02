"""M9 repository smoke checks; CDK assertions live beside the TypeScript app."""

from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.m9
def test_cdk_app_and_assertions_are_checked_in() -> None:
    """The cumulative gate must never silently pass without an infra project and tests."""
    infra = REPOSITORY_ROOT / "infra"
    assert (infra / "cdk.json").is_file()
    assert (infra / "package-lock.json").is_file()
    assert (infra / "lib" / "deadbolt-stack.ts").is_file()
    assert (infra / "test" / "m9.test.ts").is_file()
