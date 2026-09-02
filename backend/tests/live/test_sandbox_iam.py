"""Explicit AWS sandbox IAM rehearsal; never selected by the normal CI gate."""

from __future__ import annotations

import os

import pytest

from deadbolt.providers.aws_iam import AWSIAMProvider


@pytest.mark.live
def test_sandbox_iam_revoke_restore() -> None:
    user_name = os.environ.get("DEADBOLT_LIVE_IAM_USER")
    if not user_name:
        pytest.skip("set DEADBOLT_LIVE_IAM_USER to a throwaway sandbox IAM user")
    provider = AWSIAMProvider(region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    entitlements = tuple(item for item in provider.snapshot() if item.identity_id == user_name)
    if not entitlements:
        pytest.skip("throwaway sandbox user has no attached or inline policy")
    entitlement = entitlements[0]
    result = provider.revoke(entitlement, dry_run=False)
    assert result.ok
    restored = provider.restore(result.pre_image or {})
    assert restored.ok
