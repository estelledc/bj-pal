"""Identity-aware, fail-closed authentication for durable job routes."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi.security import HTTPAuthorizationCredentials


MIN_CONTROL_TOKEN_LENGTH = 32
DEFAULT_TENANT_ACTIVE_JOB_LIMIT = 100
DEFAULT_TENANT_SUBMISSION_LIMIT_PER_MINUTE = 60
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
TOKEN_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")

JOBS_SUBMIT = "jobs:submit"
JOBS_READ = "jobs:read"
JOBS_CONTROL = "jobs:control"
JOBS_REPLAY = "jobs:replay"
OPERATIONS_REQUEST = "operations:request"
OPERATIONS_READ = "operations:read"
OPERATIONS_APPROVE = "operations:approve"
OPERATIONS_RECONCILE = "operations:reconcile"
TRIALS_MANAGE = "trials:manage"
TRIALS_READ = "trials:read"
CONTROL_SCOPES = frozenset(
    {
        JOBS_SUBMIT,
        JOBS_READ,
        JOBS_CONTROL,
        JOBS_REPLAY,
        OPERATIONS_REQUEST,
        OPERATIONS_READ,
        OPERATIONS_APPROVE,
        OPERATIONS_RECONCILE,
        TRIALS_MANAGE,
        TRIALS_READ,
    }
)


class ControlPlaneNotConfigured(RuntimeError):
    """The server has no valid control-plane credential registry."""


class ControlPlaneUnauthorized(PermissionError):
    """The caller did not present a registered control-plane credential."""


class ControlPlaneForbidden(PermissionError):
    """The authenticated principal is not allowed to perform this operation."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ControlPrincipal:
    principal_id: str
    tenant_id: str
    scopes: frozenset[str]
    max_priority: int
    tenant_active_job_limit: int = DEFAULT_TENANT_ACTIVE_JOB_LIMIT
    tenant_submission_limit_per_minute: int = (
        DEFAULT_TENANT_SUBMISSION_LIMIT_PER_MINUTE
    )

    def __post_init__(self) -> None:
        if not IDENTIFIER_PATTERN.fullmatch(self.principal_id):
            raise ValueError("principal_id must contain 1-64 safe characters")
        if not IDENTIFIER_PATTERN.fullmatch(self.tenant_id):
            raise ValueError("tenant_id must contain 1-64 safe characters")
        if not self.scopes or not self.scopes <= CONTROL_SCOPES:
            raise ValueError("principal scopes must be non-empty and supported")
        if (
            isinstance(self.max_priority, bool)
            or not isinstance(self.max_priority, int)
            or not 0 <= self.max_priority <= 9
        ):
            raise ValueError("principal max_priority must be an integer between 0 and 9")
        if (
            isinstance(self.tenant_active_job_limit, bool)
            or not isinstance(self.tenant_active_job_limit, int)
            or not 1 <= self.tenant_active_job_limit <= 10_000
        ):
            raise ValueError("tenant_active_job_limit must be between 1 and 10000")
        if (
            isinstance(self.tenant_submission_limit_per_minute, bool)
            or not isinstance(self.tenant_submission_limit_per_minute, int)
            or not 1 <= self.tenant_submission_limit_per_minute <= 10_000
        ):
            raise ValueError(
                "tenant_submission_limit_per_minute must be between 1 and 10000"
            )

    def require_priority(self, priority: int) -> None:
        if priority > self.max_priority:
            raise ControlPlaneForbidden(
                code="priority_forbidden",
                message="The requested job priority exceeds this principal's limit.",
            )


@dataclass(frozen=True)
class ControlPlaneCredential:
    token_sha256: str
    principal: ControlPrincipal

    def __post_init__(self) -> None:
        if not TOKEN_SHA256_PATTERN.fullmatch(self.token_sha256):
            raise ValueError("control credential token_sha256 must be 64 lowercase hex characters")

    @classmethod
    def from_token(cls, *, token: str, principal: ControlPrincipal) -> "ControlPlaneCredential":
        if len(token) < MIN_CONTROL_TOKEN_LENGTH:
            raise ValueError("control token must contain at least 32 characters")
        return cls(
            token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            principal=principal,
        )


@dataclass(frozen=True)
class ControlPlaneAuthenticator:
    credentials: tuple[ControlPlaneCredential, ...]
    configuration_error: bool = False

    @classmethod
    def from_configuration(
        cls,
        *,
        legacy_token: str | None,
        registry_json: str | None,
        credentials: tuple[ControlPlaneCredential, ...] | None = None,
    ) -> "ControlPlaneAuthenticator":
        try:
            if credentials is not None:
                resolved = credentials
            elif registry_json:
                resolved = _parse_registry(registry_json)
            elif legacy_token is not None:
                legacy = ControlPrincipal(
                    principal_id="legacy-control",
                    tenant_id="default",
                    scopes=CONTROL_SCOPES,
                    max_priority=9,
                )
                resolved = (
                    ControlPlaneCredential.from_token(
                        token=legacy_token,
                        principal=legacy,
                    ),
                )
            else:
                resolved = ()
            _validate_credentials(resolved)
            return cls(credentials=resolved)
        except (TypeError, ValueError, json.JSONDecodeError):
            return cls(credentials=(), configuration_error=True)

    @property
    def configured(self) -> bool:
        return bool(self.credentials) and not self.configuration_error

    def authorize(
        self,
        credentials: HTTPAuthorizationCredentials | None,
        *,
        required_scope: str,
    ) -> ControlPrincipal:
        if not self.configured:
            raise ControlPlaneNotConfigured
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise ControlPlaneUnauthorized
        candidate_sha = hashlib.sha256(credentials.credentials.encode("utf-8")).hexdigest()
        matched: ControlPrincipal | None = None
        for registered in self.credentials:
            if hmac.compare_digest(candidate_sha, registered.token_sha256):
                matched = registered.principal
        if matched is None:
            raise ControlPlaneUnauthorized
        if required_scope not in matched.scopes:
            raise ControlPlaneForbidden(
                code="control_plane_forbidden",
                message="The authenticated principal lacks the required control scope.",
            )
        return matched


def _parse_registry(raw: str) -> tuple[ControlPlaneCredential, ...]:
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != {"principals"}:
        raise ValueError("control registry must contain only principals")
    entries = payload["principals"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("control registry principals must be a non-empty list")
    credentials = []
    for entry in entries:
        required_fields = {
            "principal_id",
            "tenant_id",
            "token_sha256",
            "scopes",
            "max_priority",
        }
        optional_fields = {
            "tenant_active_job_limit",
            "tenant_submission_limit_per_minute",
        }
        if (
            not isinstance(entry, dict)
            or not required_fields <= set(entry)
            or set(entry) - required_fields - optional_fields
        ):
            raise ValueError("control registry principal fields are invalid")
        scopes = entry["scopes"]
        if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
            raise ValueError("control registry scopes must be a string list")
        principal = ControlPrincipal(
            principal_id=_required_string(entry, "principal_id"),
            tenant_id=_required_string(entry, "tenant_id"),
            scopes=frozenset(scopes),
            max_priority=entry["max_priority"],
            tenant_active_job_limit=entry.get(
                "tenant_active_job_limit",
                DEFAULT_TENANT_ACTIVE_JOB_LIMIT,
            ),
            tenant_submission_limit_per_minute=entry.get(
                "tenant_submission_limit_per_minute",
                DEFAULT_TENANT_SUBMISSION_LIMIT_PER_MINUTE,
            ),
        )
        credentials.append(
            ControlPlaneCredential(
                token_sha256=_required_string(entry, "token_sha256"),
                principal=principal,
            )
        )
    resolved = tuple(credentials)
    _validate_credentials(resolved)
    return resolved


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"control registry {key} must be a string")
    return value


def _validate_credentials(credentials: tuple[ControlPlaneCredential, ...]) -> None:
    token_hashes = [credential.token_sha256 for credential in credentials]
    if len(token_hashes) != len(set(token_hashes)):
        raise ValueError("control credential token hashes must be unique")
    principals: dict[str, ControlPrincipal] = {}
    tenant_policies: dict[str, tuple[int, int]] = {}
    for credential in credentials:
        principal = credential.principal
        existing_principal = principals.setdefault(principal.principal_id, principal)
        if existing_principal != principal:
            raise ValueError("one principal_id must resolve to one principal policy")
        policy = (
            principal.tenant_active_job_limit,
            principal.tenant_submission_limit_per_minute,
        )
        existing_policy = tenant_policies.setdefault(principal.tenant_id, policy)
        if existing_policy != policy:
            raise ValueError("all principals in one tenant must share one admission policy")
