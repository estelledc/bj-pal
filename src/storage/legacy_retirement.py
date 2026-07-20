"""Read-only audit for retiring the historical shared SQLite state store."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import prediction_feedback, state_layout, user_memory
from .verified_copy import (
    DomainSpec,
    connect_read_only,
    metadata_body,
    metadata_valid,
    snapshot,
    table_names,
)


ROOT = Path(__file__).resolve().parent.parent.parent
STATE_LAYOUT_POLICY_ENV = "BJ_PAL_STATE_LAYOUT_POLICY"
COMPATIBILITY_POLICY = "compatibility"
DEDICATED_REQUIRED_POLICY = "dedicated_required"
VALID_POLICIES = frozenset({COMPATIBILITY_POLICY, DEDICATED_REQUIRED_POLICY})

KNOWN_LEGACY_TABLES = frozenset(
    {
        "tool_calls",
        "plan_trace",
        "plan_outcome",
        "prediction_log",
        "user_memory",
        "user_memory_events",
    }
)


@dataclass(frozen=True)
class LegacyRetirementAudit:
    ready: bool
    policy: str
    legacy_database_name: str
    legacy_counts: dict[str, int]
    resolved_database_names: dict[str, str]
    checks: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "policy": self.policy,
            "legacy_database_name": self.legacy_database_name,
            "legacy_counts": dict(self.legacy_counts),
            "resolved_database_names": dict(self.resolved_database_names),
            "checks": dict(self.checks),
        }


@dataclass(frozen=True)
class _DomainBinding:
    name: str
    spec: DomainSpec
    legacy_path: Callable[[], Path]
    resolver: Callable[[], Path]


def state_layout_policy() -> str:
    policy = os.environ.get(STATE_LAYOUT_POLICY_ENV, COMPATIBILITY_POLICY).strip()
    if policy not in VALID_POLICIES:
        raise ValueError(
            f"{STATE_LAYOUT_POLICY_ENV} must be one of {sorted(VALID_POLICIES)}"
        )
    return policy


def _bindings() -> tuple[_DomainBinding, ...]:
    return (
        _DomainBinding(
            "plan_evidence",
            state_layout.PLAN_EVIDENCE_SPEC,
            lambda: state_layout.LEGACY_SHARED_DB,
            state_layout.resolve_plan_evidence_path,
        ),
        _DomainBinding(
            "prediction_feedback",
            prediction_feedback.PREDICTION_FEEDBACK_SPEC,
            lambda: prediction_feedback.LEGACY_SHARED_DB,
            prediction_feedback.resolve_prediction_feedback_path,
        ),
        _DomainBinding(
            "user_memory",
            user_memory.USER_MEMORY_SPEC,
            lambda: user_memory.LEGACY_SHARED_DB,
            user_memory.resolve_user_memory_path,
        ),
    )


def _quick_check(path: Path) -> bool:
    try:
        with connect_read_only(path) as connection:
            return connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except (OSError, sqlite3.Error):
        return False


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _metadata(path: Path, spec: DomainSpec) -> dict[str, Any] | None:
    try:
        with connect_read_only(path) as connection:
            if "state_store_metadata" not in table_names(connection):
                return None
            row = connection.execute(
                "SELECT * FROM state_store_metadata WHERE domain=?",
                (spec.domain,),
            ).fetchone()
            return metadata_body(row) if row is not None else None
    except (OSError, sqlite3.Error, ValueError):
        return None


def inspect_legacy_retirement(
    *,
    legacy_path: Path | None = None,
    tool_audit_path: Path | None = None,
    policy: str | None = None,
) -> LegacyRetirementAudit:
    """Prove every mutable owner resolves away from legacy without reading payloads."""

    selected_policy = policy or state_layout_policy()
    if selected_policy not in VALID_POLICIES:
        raise ValueError(f"unsupported state layout policy: {selected_policy}")

    bindings = _bindings()
    legacy_candidates = {binding.legacy_path().resolve() for binding in bindings}
    checks: dict[str, str] = {}
    if len(legacy_candidates) != 1:
        checks["legacy_path_consistency"] = "mismatch"
        resolved_legacy = Path(legacy_path or next(iter(legacy_candidates)))
    else:
        checks["legacy_path_consistency"] = "ok"
        resolved_legacy = Path(legacy_path or next(iter(legacy_candidates)))

    legacy_tables: set[str] = set()
    legacy_counts: dict[str, int] = {}
    if not resolved_legacy.is_file():
        checks["legacy_integrity"] = "ok"
        checks["legacy_known_tables"] = "ok"
    else:
        try:
            with connect_read_only(resolved_legacy) as connection:
                checks["legacy_integrity"] = (
                    "ok"
                    if connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                    else "failed"
                )
                legacy_tables = {
                    name for name in table_names(connection) if not name.startswith("sqlite_")
                }
                unknown = sorted(legacy_tables - KNOWN_LEGACY_TABLES)
                checks["legacy_known_tables"] = (
                    "ok" if not unknown else f"unknown:{','.join(unknown)}"
                )
                for table in sorted(legacy_tables):
                    legacy_counts[table] = int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                        ).fetchone()[0]
                    )
        except sqlite3.Error:
            checks["legacy_integrity"] = "unreadable"
            checks["legacy_known_tables"] = "not_checked"

    resolved_names: dict[str, str] = {}
    for binding in bindings:
        prefix = binding.name
        resolved = Path(binding.resolver())
        resolved_names[prefix] = resolved.name
        checks[f"{prefix}_dedicated"] = (
            "ok" if resolved.resolve() != resolved_legacy.resolve() else "legacy_fallback"
        )
        checks[f"{prefix}_receipt"] = (
            "ok" if metadata_valid(resolved, binding.spec) else "invalid_or_missing"
        )
        checks[f"{prefix}_integrity"] = (
            "ok" if resolved.is_file() and _quick_check(resolved) else "unreadable"
        )

        source_has_domain = set(binding.spec.table_columns) <= legacy_tables and any(
            legacy_counts.get(table, 0) for table in binding.spec.table_columns
        )
        if not source_has_domain:
            checks[f"{prefix}_legacy_binding"] = "ok"
            continue
        body = _metadata(resolved, binding.spec)
        try:
            with connect_read_only(resolved_legacy) as connection:
                current_source = snapshot(connection, binding.spec)
        except (OSError, sqlite3.Error, ValueError):
            current_source = None
        bound = bool(
            body
            and body["origin"] == "migrated_copy"
            and body["source_name"] == resolved_legacy.name
            and current_source is not None
            and body["source_counts"] == current_source["counts"]
            and body["source_digests"] == current_source["digests"]
        )
        checks[f"{prefix}_legacy_binding"] = "ok" if bound else "source_drift"

    configured_tool_audit = Path(
        tool_audit_path
        or os.environ.get("BJ_PAL_TOOL_AUDIT_DB")
        or ROOT / "runtime" / "tool_audit.db"
    )
    resolved_names["tool_audit"] = configured_tool_audit.name
    checks["tool_audit_dedicated"] = (
        "ok"
        if configured_tool_audit.resolve() != resolved_legacy.resolve()
        else "legacy_fallback"
    )
    if configured_tool_audit.exists():
        checks["tool_audit_integrity"] = (
            "ok" if _quick_check(configured_tool_audit) else "unreadable"
        )
        try:
            with connect_read_only(configured_tool_audit) as connection:
                owned_tables = {
                    name
                    for name in table_names(connection)
                    if not name.startswith("sqlite_")
                }
            checks["tool_audit_owner"] = (
                "ok" if owned_tables == {"tool_calls"} else "unexpected_tables"
            )
        except sqlite3.Error:
            checks["tool_audit_owner"] = "unreadable"
    else:
        checks["tool_audit_integrity"] = "ok"
        checks["tool_audit_owner"] = "ok"

    return LegacyRetirementAudit(
        ready=all(value == "ok" for value in checks.values()),
        policy=selected_policy,
        legacy_database_name=resolved_legacy.name,
        legacy_counts=legacy_counts,
        resolved_database_names=resolved_names,
        checks=checks,
    )
