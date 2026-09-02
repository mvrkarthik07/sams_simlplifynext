"""AWS IAM Tier-A connector.

The connector deliberately keeps the AWS client behind a constructor boundary.  That makes
the normal test path moto-backed and prevents importing this module from creating a live client.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import unquote

import boto3  # type: ignore[import-untyped]  # boto3 does not publish strict typing metadata.
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from deadbolt.contracts.models import ActionResult, CredentialType, Entitlement, Scope
from deadbolt.errors import ProviderError


class _IamClient(Protocol):
    def list_users(self, **kwargs: object) -> object: ...

    def list_attached_user_policies(self, **kwargs: object) -> object: ...

    def list_user_policies(self, **kwargs: object) -> object: ...

    def get_user_policy(self, **kwargs: object) -> object: ...

    def generate_service_last_accessed_details(self, **kwargs: object) -> object: ...

    def get_service_last_accessed_details(self, **kwargs: object) -> object: ...

    def simulate_principal_policy(self, **kwargs: object) -> object: ...

    def detach_user_policy(self, **kwargs: object) -> object: ...

    def delete_user_policy(self, **kwargs: object) -> object: ...

    def attach_user_policy(self, **kwargs: object) -> object: ...

    def put_user_policy(self, **kwargs: object) -> object: ...


class _IamOperation(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


_Sleep = Callable[[float], None]
_ManagedPolicy = dict[str, object]
_Advisor = dict[str, datetime | None]
_ACCOUNT_INDEX = 4


def _error_code(error: ClientError) -> str:
    response = error.response
    if isinstance(response, Mapping):
        error_part = response.get("Error")
        if isinstance(error_part, Mapping):
            code = error_part.get("Code")
            if isinstance(code, str):
                return code
    return "Unknown"


def _is_no_such_entity(error: ClientError) -> bool:
    return _error_code(error) == "NoSuchEntity"


def _aware(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    return None


def _json_document(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        decoded = unquote(value)
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError:
            return decoded
        return parsed
    return value


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _policy_actions(document: object) -> tuple[str, ...]:
    if not isinstance(document, Mapping):
        return ()
    statements = document.get("Statement", ())
    if isinstance(statements, Mapping):
        statements = (statements,)
    if not isinstance(statements, Sequence) or isinstance(statements, (str, bytes)):
        return ()
    actions: list[str] = []
    for statement in statements:
        if not isinstance(statement, Mapping):
            continue
        action = statement.get("Action")
        values = (action,) if isinstance(action, str) else action
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            actions.extend(item for item in values if isinstance(item, str))
    return tuple(sorted(set(actions), key=lambda item: item.encode("utf-8")))


def _scope(policy_name: str, actions: Iterable[str] = ()) -> Scope:
    name = policy_name.lower()
    action_values = tuple(actions)
    if "administrator" in name or name in {"poweruseraccess", "admin"}:
        return Scope.ADMIN
    if any(action == "*" or action.endswith(":*") for action in action_values):
        return Scope.ADMIN
    read_prefixes = (
        "get",
        "list",
        "describe",
        "head",
        "read",
        "view",
        "check",
        "lookup",
    )
    if any(
        not action.split(":", 1)[-1].lower().startswith(read_prefixes) for action in action_values
    ):
        return Scope.WRITE
    if "readonly" in name or "read-only" in name or action_values:
        return Scope.READ
    return Scope.WRITE


def _sort_key(item: Entitlement) -> tuple[bytes, bytes, bytes]:
    return (
        item.identity_id.encode("utf-8"),
        item.resource.encode("utf-8"),
        item.scope.value.encode("utf-8"),
    )


class AWSIAMProvider:
    """Read and safely mutate user policy entitlements in AWS IAM."""

    system = "aws-iam"

    def __init__(
        self,
        iam_client: object | None = None,
        *,
        region_name: str | None = None,
        advisor_max_polls: int = 8,
        advisor_poll_seconds: float = 0.2,
        sleeper: _Sleep | None = None,
    ) -> None:
        if advisor_max_polls < 1:
            raise ValueError("advisor_max_polls must be positive")
        self._client = cast(
            _IamClient,
            iam_client if iam_client is not None else boto3.client("iam", region_name=region_name),
        )
        self._max_polls = advisor_max_polls
        self._poll_seconds = advisor_poll_seconds
        self._sleep = sleeper or time.sleep
        self._advisor_cache: dict[str, _Advisor] = {}

    def _response(self, operation: _IamOperation, **kwargs: object) -> Mapping[str, object]:
        try:
            response = operation(**kwargs)
        except ClientError as exc:
            raise ProviderError(f"AWS IAM operation failed: {_error_code(exc)}") from exc
        if not isinstance(response, Mapping):
            raise ProviderError("AWS IAM operation returned a non-object response")
        return response

    def _list(
        self,
        operation: _IamOperation,
        result_key: str,
        **kwargs: object,
    ) -> tuple[Mapping[str, object], ...]:
        values: list[Mapping[str, object]] = []
        request = dict(kwargs)
        while True:
            response = self._response(operation, **request)
            page = response.get(result_key, ())
            if isinstance(page, Sequence) and not isinstance(page, (str, bytes)):
                values.extend(item for item in page if isinstance(item, Mapping))
            if not response.get("IsTruncated"):
                return tuple(values)
            marker = response.get("Marker")
            if not isinstance(marker, str) or not marker:
                return tuple(values)
            request["Marker"] = marker

    def _advisor(self, user_arn: str) -> _Advisor:  # noqa: PLR0912 — bounded polling has explicit terminal states.
        cached = self._advisor_cache.get(user_arn)
        if cached is not None:
            return cached
        try:
            generated = self._client.generate_service_last_accessed_details(Arn=user_arn)
        except NotImplementedError:
            # moto versions used in CI do not implement Access Advisor. Unknown usage is
            # represented as None; a timestamp is never synthesized.
            self._advisor_cache[user_arn] = {}
            return {}
        except ClientError as exc:
            raise ProviderError(
                f"AWS IAM Access Advisor generation failed: {_error_code(exc)}"
            ) from exc
        if not isinstance(generated, Mapping):
            raise ProviderError("AWS IAM Access Advisor returned a non-object job")
        job_id = generated.get("JobId")
        if not isinstance(job_id, str) or not job_id:
            raise ProviderError("AWS IAM Access Advisor response omitted JobId")

        response: Mapping[str, object] = {}
        for poll in range(self._max_polls):
            try:
                candidate = self._client.get_service_last_accessed_details(JobId=job_id)
            except ClientError as exc:
                raise ProviderError(
                    f"AWS IAM Access Advisor polling failed: {_error_code(exc)}"
                ) from exc
            if not isinstance(candidate, Mapping):
                raise ProviderError("AWS IAM Access Advisor returned a non-object result")
            response = candidate
            status = _text(candidate.get("JobStatus"), "COMPLETED")
            if status == "COMPLETED":
                break
            if status == "FAILED":  # pragma: no cover - defensive AWS status handling.
                raise ProviderError(f"AWS IAM Access Advisor job {job_id} ended with {status}")
            if poll + 1 < self._max_polls:
                self._sleep(self._poll_seconds)
        else:
            raise ProviderError(f"AWS IAM Access Advisor job {job_id} exceeded poll bound")

        details: dict[str, datetime | None] = {}
        service_items = response.get("ServicesLastAccessed", ())
        if isinstance(service_items, Sequence) and not isinstance(service_items, (str, bytes)):
            for item in service_items:
                if not isinstance(item, Mapping):
                    continue
                service = _text(item.get("ServiceName"))
                if service:
                    details[service] = _aware(item.get("LastAuthenticated"))
        self._advisor_cache[user_arn] = details
        return details

    @staticmethod
    def _service_match(policy_name: str, actions: Iterable[str], service: str) -> bool:
        compact_service = "".join(character for character in service.lower() if character.isalnum())
        compact_policy = "".join(
            character for character in policy_name.lower() if character.isalnum()
        )
        service_names = {
            compact_service,
            compact_service.removeprefix("amazon"),
            compact_service.removeprefix("aws"),
        }
        if any(name and name in compact_policy for name in service_names):
            return True
        service_prefixes = {action.split(":", 1)[0].lower() for action in actions if ":" in action}
        return any(
            prefix in compact_service or compact_service.startswith(prefix)
            for prefix in service_prefixes
        )

    @classmethod
    def _last_used(
        cls, policy_name: str, actions: Iterable[str], advisor: Mapping[str, datetime | None]
    ) -> datetime | None:
        matches = [
            timestamp
            for service, timestamp in advisor.items()
            if timestamp is not None and cls._service_match(policy_name, actions, service)
        ]
        return max(matches) if matches else None

    def snapshot(self) -> Iterable[Entitlement]:
        entitlements: list[Entitlement] = []
        users = self._list(self._client.list_users, "Users")
        for user in users:
            user_name = _text(user.get("UserName"))
            user_arn = _text(user.get("Arn"))
            if not user_name or not user_arn:
                continue
            advisor = self._advisor(user_arn)
            created_at = _aware(user.get("CreateDate"))
            managed = self._list(
                self._client.list_attached_user_policies,
                "AttachedPolicies",
                UserName=user_name,
            )
            for policy in managed:
                policy_name = _text(policy.get("PolicyName"))
                policy_arn = _text(policy.get("PolicyArn"))
                if not policy_name or not policy_arn:
                    continue
                entitlements.append(
                    Entitlement(
                        user_name,
                        self.system,
                        policy_arn,
                        _scope(policy_name),
                        created_at,
                        self._last_used(policy_name, (), advisor),
                        CredentialType.FEDERATED,
                        True,
                        {
                            "kind": "managed",
                            "user_name": user_name,
                            "user_arn": user_arn,
                            "policy_name": policy_name,
                            "policy_arn": policy_arn,
                            "attachment": dict(policy),
                        },
                    )
                )
            inline_names = self._policy_names(user_name)
            for policy_name in inline_names:
                if not policy_name:
                    continue
                document = self._inline_document(user_name, policy_name)
                actions = _policy_actions(document)
                account_parts = user_arn.split(":")
                account = (
                    account_parts[_ACCOUNT_INDEX] if len(account_parts) > _ACCOUNT_INDEX else ""
                )
                resource = f"arn:aws:iam::{account}:user/{user_name}/policy/{policy_name}"
                entitlements.append(
                    Entitlement(
                        user_name,
                        self.system,
                        resource,
                        _scope(policy_name, actions),
                        created_at,
                        self._last_used(policy_name, actions, advisor),
                        CredentialType.FEDERATED,
                        True,
                        {
                            "kind": "inline",
                            "user_name": user_name,
                            "user_arn": user_arn,
                            "policy_name": policy_name,
                            "policy_document": document,
                            "actions": actions,
                        },
                    )
                )
        return tuple(sorted(entitlements, key=_sort_key))

    def _inline_document(self, user_name: str, policy_name: str) -> object:
        try:
            response = self._client.get_user_policy(UserName=user_name, PolicyName=policy_name)
        except ClientError as exc:
            if _is_no_such_entity(exc):
                return {}
            raise ProviderError(f"AWS IAM GetUserPolicy failed: {_error_code(exc)}") from exc
        if not isinstance(response, Mapping):
            raise ProviderError("AWS IAM GetUserPolicy returned a non-object response")
        return _json_document(response.get("PolicyDocument", {}))

    def _policy_names(self, user_name: str) -> tuple[str, ...]:
        names: list[str] = []
        marker: str | None = None
        while True:
            request: dict[str, object] = {"UserName": user_name}
            if marker:
                request["Marker"] = marker
            response = self._response(self._client.list_user_policies, **request)
            values = response.get("PolicyNames", ())
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                names.extend(value for value in values if isinstance(value, str))
            if not response.get("IsTruncated"):
                return tuple(names)
            next_marker = response.get("Marker")
            marker = next_marker if isinstance(next_marker, str) and next_marker else None
            if marker is None:
                return tuple(names)

    @staticmethod
    def _pre_image(e: Entitlement) -> _ManagedPolicy:
        raw = dict(e.raw)
        kind = _text(raw.get("kind"))
        if kind not in {"managed", "inline"}:
            raise ProviderError("AWS IAM entitlement lacks a policy kind")
        pre: _ManagedPolicy = {
            "provider": "aws-iam",
            "kind": kind,
            "system": e.system,
            "identity_id": e.identity_id,
            "resource": e.resource,
            "scope": e.scope.value,
            "user_name": raw.get("user_name", e.identity_id),
            "policy_name": raw.get("policy_name", ""),
        }
        if kind == "managed":
            pre["policy_arn"] = raw.get("policy_arn", e.resource)
            attachment = raw.get("attachment", {})
            pre["attachment"] = dict(attachment) if isinstance(attachment, Mapping) else {}
        else:
            document = _json_document(raw.get("policy_document"))
            if not isinstance(document, Mapping):
                raise ProviderError("inline AWS IAM pre-image lacks the full policy document")
            pre["policy_document"] = dict(document)
        return pre

    def _simulate(self, e: Entitlement, pre_image: Mapping[str, object]) -> None:
        raw = e.raw
        actions = raw.get("actions", ())
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
            return
        action_names = [action for action in actions if isinstance(action, str)]
        if not action_names:
            return
        user_arn = _text(raw.get("user_arn"))
        if not user_arn:
            return
        try:
            self._client.simulate_principal_policy(
                PolicySourceArn=user_arn,
                ActionNames=action_names,
            )
        except ClientError as exc:
            # Simulation is advisory; an IAM permission boundary can deny simulation while
            # still allowing the explicitly requested policy mutation.
            if _error_code(exc) == "NoSuchEntity":
                return
            raise ProviderError(f"AWS IAM simulation failed: {_error_code(exc)}") from exc

    def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
        if e.system != self.system:
            raise ProviderError(f"entitlement belongs to {e.system}, not {self.system}")
        pre = self._pre_image(e)
        started = time.monotonic()
        if dry_run:
            self._simulate(e, pre)
            message = "dry-run: AWS IAM entitlement would be revoked"
            return ActionResult(
                True, self.system, e.resource, e.scope.value, True, pre, message, _ms(started)
            )
        try:
            if pre["kind"] == "managed":
                self._client.detach_user_policy(
                    UserName=_text(pre.get("user_name")), PolicyArn=_text(pre.get("policy_arn"))
                )
            else:
                self._client.delete_user_policy(
                    UserName=_text(pre.get("user_name")), PolicyName=_text(pre.get("policy_name"))
                )
        except ClientError as exc:
            if _is_no_such_entity(exc):
                return ActionResult(
                    True,
                    self.system,
                    e.resource,
                    e.scope.value,
                    False,
                    pre,
                    "entitlement already revoked",
                    _ms(started),
                )
            raise ProviderError(f"AWS IAM revoke failed: {_error_code(exc)}") from exc
        return ActionResult(
            True,
            self.system,
            e.resource,
            e.scope.value,
            False,
            pre,
            "entitlement revoked",
            _ms(started),
        )

    def restore(self, pre_image: Mapping[str, object]) -> ActionResult:
        kind = _text(pre_image.get("kind"))
        if _text(pre_image.get("provider"), "aws-iam") != "aws-iam" or kind not in {
            "managed",
            "inline",
        }:
            raise ProviderError("invalid AWS IAM pre-image")
        user_name = _text(pre_image.get("user_name"))
        policy_name = _text(pre_image.get("policy_name"))
        if not user_name or not policy_name:
            raise ProviderError("AWS IAM pre-image lacks user or policy name")
        resource = _text(pre_image.get("resource"))
        scope = _text(pre_image.get("scope"), Scope.READ.value)
        started = time.monotonic()
        try:
            if kind == "managed":
                arn = _text(pre_image.get("policy_arn"))
                self._client.attach_user_policy(UserName=user_name, PolicyArn=arn)
                attached = self._list(
                    self._client.list_attached_user_policies,
                    "AttachedPolicies",
                    UserName=user_name,
                )
                verified = any(_text(item.get("PolicyArn")) == arn for item in attached)
            else:
                document = pre_image.get("policy_document")
                if not isinstance(document, Mapping):
                    raise ProviderError("inline AWS IAM pre-image lacks policy document")
                self._client.put_user_policy(
                    UserName=user_name,
                    PolicyName=policy_name,
                    PolicyDocument=json.dumps(dict(document)),
                )
                names = self._policy_names(user_name)
                verified_document = self._inline_document(user_name, policy_name)
                verified = policy_name in names and verified_document == document
        except ClientError as exc:
            if _is_no_such_entity(exc):
                return ActionResult(
                    True,
                    self.system,
                    resource,
                    scope,
                    False,
                    dict(pre_image),
                    "already restored or user absent",
                    _ms(started),
                )
            raise ProviderError(f"AWS IAM restore failed: {_error_code(exc)}") from exc
        if not verified:
            raise ProviderError("AWS IAM restore verification failed")
        return ActionResult(
            True,
            self.system,
            resource,
            scope,
            False,
            dict(pre_image),
            "entitlement restored and verified",
            _ms(started),
        )


def _ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


AwsIamProvider = AWSIAMProvider

__all__ = ["AWSIAMProvider", "AwsIamProvider"]
