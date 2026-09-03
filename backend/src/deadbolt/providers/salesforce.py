"""Salesforce JWT-bearer connector with permission-assignment granularity."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx
import jwt

from deadbolt.contracts.models import ActionResult, CredentialType, Entitlement, Scope
from deadbolt.errors import ProviderError

_HTTP_BAD_REQUEST = 400


class _Client(Protocol):
    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response: ...


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProviderError("Salesforce returned a non-object payload")
    return value


def _nested(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _records(payload: object) -> tuple[Mapping[str, object], ...]:
    body = _mapping(payload)
    values = body.get("records", ())
    if not isinstance(values, list):
        raise ProviderError("Salesforce query response has no records array")
    return tuple(item for item in values if isinstance(item, Mapping))


class SalesforceProvider:
    """Salesforce REST/SOQL provider using a Connected App JWT bearer flow."""

    system = "salesforce"

    def __init__(  # noqa: PLR0913 — JWT and API endpoint configuration is explicit.
        self,
        *,
        instance_url: str | None = None,
        client_id: str | None = None,
        username: str | None = None,
        private_key: str | None = None,
        api_version: str = "v61.0",
        login_url: str = "https://login.salesforce.com",
        access_token: str | None = None,
        client: object | None = None,
    ) -> None:
        self.instance_url = (instance_url or os.environ.get("SALESFORCE_INSTANCE_URL", "")).rstrip(
            "/"
        )
        self.client_id = client_id or os.environ.get("SALESFORCE_CLIENT_ID", "")
        self.username = username or os.environ.get("SALESFORCE_USERNAME", "")
        self.private_key = private_key or os.environ.get("SALESFORCE_PRIVATE_KEY", "")
        self.api_version = api_version
        self.login_url = login_url.rstrip("/")
        self._token = access_token or os.environ.get("SALESFORCE_ACCESS_TOKEN", "")
        self._client = cast(_Client, client if client is not None else httpx.Client())

    def _jwt_assertion(self) -> str:
        if not self.client_id or not self.username or not self.private_key:
            raise ProviderError(
                "Salesforce JWT config requires client_id, username, and private_key"
            )
        return str(
            jwt.encode(
                {
                    "iss": self.client_id,
                    "sub": self.username,
                    "aud": self.login_url,
                    "exp": int(time.time()) + 180,
                },
                self.private_key,
                algorithm="RS256",
            )
        )

    def _authenticate(self) -> None:
        response = self._client.request(
            "POST",
            f"{self.login_url}/services/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": self._jwt_assertion(),
            },
        )
        if response.status_code >= _HTTP_BAD_REQUEST:
            raise ProviderError(
                f"Salesforce JWT authentication failed with HTTP {response.status_code}"
            )
        payload = _mapping(response.json())
        token = payload.get("access_token")
        instance = payload.get("instance_url")
        if not isinstance(token, str) or not isinstance(instance, str):
            raise ProviderError("Salesforce JWT response omitted access_token or instance_url")
        self._token, self.instance_url = token, instance.rstrip("/")

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        if not self._token:
            self._authenticate()
        if not self.instance_url:
            raise ProviderError("Salesforce instance_url is required")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        response = self._client.request(
            method, f"{self.instance_url}{path}", headers=headers, **kwargs
        )
        if response.status_code >= _HTTP_BAD_REQUEST:
            raise ProviderError(
                f"Salesforce {method} {path} failed with HTTP {response.status_code}"
            )
        return response

    def _query(self, soql: str) -> tuple[Mapping[str, object], ...]:
        path = f"/services/data/{self.api_version}/query"
        records: list[Mapping[str, object]] = []
        response = self._request("GET", path, params={"q": soql})
        while True:
            payload = _mapping(response.json())
            records.extend(_records(payload))
            next_url = payload.get("nextRecordsUrl")
            if not isinstance(next_url, str) or not next_url:
                return tuple(records)
            response = self._request("GET", next_url)

    def snapshot(self) -> tuple[Entitlement, ...]:
        assignments = self._query(
            "SELECT Id,AssigneeId,Assignee.Username,Assignee.Email,PermissionSetId,"
            "PermissionSet.Name,PermissionSet.PermissionsModifyAllData,"
            "PermissionSet.PermissionsViewAllData,PermissionSet.PermissionsManageUsers "
            "FROM PermissionSetAssignment"
        )
        objects = self._query(
            "SELECT ParentId,SobjectType,PermissionsRead,PermissionsCreate,PermissionsEdit,"
            "PermissionsDelete,PermissionsModifyAllRecords,PermissionsViewAllRecords "
            "FROM ObjectPermissions"
        )
        fields = self._query(
            "SELECT ParentId,SobjectType,Field,PermissionsRead,PermissionsEdit "
            "FROM FieldPermissions"
        )
        logins = self._query("SELECT UserId,LoginTime FROM LoginHistory ORDER BY LoginTime DESC")
        last_by_user = {_text(item.get("UserId")): _time(item.get("LoginTime")) for item in logins}
        assignment_by_id = {_text(item.get("PermissionSetId")): item for item in assignments}
        result: list[Entitlement] = []
        for record in assignments:
            assignment_id = _text(record.get("Id"))
            user = _nested(record.get("Assignee"))
            identity = _text(user.get("Email"), _text(user.get("Username")))
            if not assignment_id or not identity:
                continue
            permission_set = _nested(record.get("PermissionSet"))
            dangerous = any(
                bool(permission_set.get(key))
                for key in (
                    "PermissionsModifyAllData",
                    "PermissionsViewAllData",
                    "PermissionsManageUsers",
                    "PermissionsAuthorApex",
                    "PermissionsViewEncryptedData",
                )
            )
            result.append(
                Entitlement(
                    identity.lower(),
                    self.system,
                    f"salesforce:permission-set:{_text(permission_set.get('Name'), assignment_id)}",
                    Scope.ADMIN if dangerous else Scope.WRITE,
                    None,
                    last_by_user.get(_text(record.get("AssigneeId"))),
                    CredentialType.FEDERATED,
                    True,
                    {
                        "kind": "permission-set-assignment",
                        "assignment_id": assignment_id,
                        "assignee_id": _text(record.get("AssigneeId")),
                        "permission_set_id": _text(record.get("PermissionSetId")),
                        "permission_set": dict(permission_set),
                        "email": identity.lower(),
                        "reversible": True,
                    },
                )
            )
        for permission in objects:
            parent_id = _text(permission.get("ParentId"))
            assignment = assignment_by_id.get(parent_id)
            if assignment is None:
                continue
            user = _nested(assignment.get("Assignee"))
            identity = _text(user.get("Email"), _text(user.get("Username")))
            object_name = _text(permission.get("SobjectType"))
            if not identity or not object_name:
                continue
            privilege = (
                "ModifyAllRecords" if permission.get("PermissionsModifyAllRecords") else "Read"
            )
            result.append(
                Entitlement(
                    identity.lower(),
                    self.system,
                    f"salesforce:object:{object_name}",
                    Scope.ADMIN if privilege == "ModifyAllRecords" else Scope.READ,
                    None,
                    last_by_user.get(_text(assignment.get("AssigneeId"))),
                    CredentialType.FEDERATED,
                    True,
                    {
                        "kind": "object-permission",
                        "assignment_id": _text(assignment.get("Id")),
                        "permission": privilege,
                        "object": object_name,
                        "permission_payload": dict(permission),
                        "email": identity.lower(),
                        "reversible": True,
                    },
                )
            )
        for field in fields:
            parent_id = _text(field.get("ParentId"))
            assignment = assignment_by_id.get(parent_id)
            if assignment is None:
                continue
            user = _nested(assignment.get("Assignee"))
            identity = _text(user.get("Email"), _text(user.get("Username")))
            field_name, object_name = _text(field.get("Field")), _text(field.get("SobjectType"))
            if identity and field_name and object_name:
                result.append(
                    Entitlement(
                        identity.lower(),
                        self.system,
                        f"salesforce:field:{field_name}",
                        Scope.READ,
                        None,
                        None,
                        CredentialType.FEDERATED,
                        True,
                        {
                            "kind": "field-permission",
                            "assignment_id": _text(assignment.get("Id")),
                            "field": field_name,
                            "object": object_name,
                            "permission_payload": dict(field),
                            "email": identity.lower(),
                            "reversible": True,
                        },
                    )
                )
        return tuple(sorted(result, key=lambda e: (e.identity_id, e.resource, e.scope.value)))

    def revoke(self, e: Entitlement, dry_run: bool) -> ActionResult:
        raw = dict(e.raw)
        if _text(raw.get("kind")) == "oauth-grant":
            raw["reversible"] = False
        if _text(raw.get("kind")) != "permission-set-assignment":
            return ActionResult(
                False,
                self.system,
                e.resource,
                e.scope.value,
                dry_run,
                raw,
                "only permission-set assignment revocation is supported",
                0,
            )
        if bool(raw.get("shared_permission_set")):
            return ActionResult(
                False,
                self.system,
                e.resource,
                e.scope.value,
                dry_run,
                raw,
                "shared PermissionSet is never mutated",
                0,
            )
        pre = {"provider": "salesforce", "resource": e.resource, "scope": e.scope.value, **raw}
        if dry_run:
            return ActionResult(
                True,
                self.system,
                e.resource,
                e.scope.value,
                True,
                pre,
                "dry-run: Salesforce assignment would be revoked",
                0,
            )
        self._request(
            "DELETE",
            f"/services/data/{self.api_version}/sobjects/PermissionSetAssignment/{raw.get('assignment_id')}",
        )
        return ActionResult(
            True,
            self.system,
            e.resource,
            e.scope.value,
            False,
            pre,
            "Salesforce assignment revoked",
            0,
        )

    def restore(self, pre_image: Mapping[str, object]) -> ActionResult:
        if _text(pre_image.get("kind")) != "permission-set-assignment":
            raise ProviderError(
                "Salesforce OAuth grants and shared permission sets are not restorable"
            )
        assignee_id, permission_set_id = (
            _text(pre_image.get("assignee_id")),
            _text(pre_image.get("permission_set_id")),
        )
        if not assignee_id or not permission_set_id:
            raise ProviderError("Salesforce assignment pre-image is incomplete")
        self._request(
            "POST",
            f"/services/data/{self.api_version}/sobjects/PermissionSetAssignment",
            json={"AssigneeId": assignee_id, "PermissionSetId": permission_set_id},
        )
        return ActionResult(
            True,
            self.system,
            _text(pre_image.get("resource")),
            _text(pre_image.get("scope")),
            False,
            dict(pre_image),
            "Salesforce assignment restored",
            0,
        )


__all__ = ["SalesforceProvider"]
