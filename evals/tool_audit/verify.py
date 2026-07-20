"""Independently recompute tool-call audit artifact claims."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


VERSION = "tool_call_audit_v2"
RESET_TOOL_NAME = "audit.session_reset"
EVENT_FIELDS = {
    "privacy_version",
    "session_id",
    "sequence",
    "previous_event_sha256",
    "timestamp",
    "tool_name",
    "params",
    "response",
    "status",
    "latency_ms",
    "redaction_count",
    "error_code",
}


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def verify_tool_audit_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported tool-audit artifact schema")
    canonical = deepcopy(artifact)
    observed_sha = canonical.pop("artifact_sha256", None)
    if observed_sha != _canonical_sha256(canonical):
        raise ValueError("tool-audit artifact SHA-256 mismatch")
    if artifact.get("classification") != "synthetic_contract":
        raise ValueError("tool-audit classification mismatch")
    if artifact.get("policy") != {
        "version": VERSION,
        "maximum_depth": 6,
        "maximum_collection_items": 25,
        "maximum_safe_text_length": 160,
        "row_mutation": "append_only",
        "session_clear": "append_reset_marker",
        "legacy_read": "payload_hidden_by_default",
        "store_scope": "independent_runtime_database",
        "legacy_migration": "no_automatic_copy",
    }:
        raise ValueError("tool-audit policy metadata mismatch")

    result = artifact.get("result") or {}
    raw_cases = result.get("raw_cases") or []
    by_case = {case.get("case_id"): case for case in raw_cases}
    if set(by_case) != {
        "privacy_projection",
        "append_only_chain",
        "reset_visibility",
        "legacy_payload_hiding",
        "storage_isolation",
    } or len(by_case) != len(raw_cases):
        raise ValueError("tool-audit case contract mismatch")

    privacy_case = by_case["privacy_projection"]
    privacy_chain = _verify_chain(privacy_case.get("events") or [])
    privacy_serialized = json.dumps(
        privacy_case.get("events") or [], ensure_ascii=False
    )
    forbidden = privacy_case.get("forbidden_event_substrings") or []
    privacy_valid = (
        privacy_chain
        and bool(forbidden)
        and all(marker not in privacy_serialized for marker in forbidden)
        and len(privacy_case["events"]) == 1
        and privacy_case["events"][0].get("error_code")
        == privacy_case.get("expected_error_code")
        and int(privacy_case["events"][0].get("redaction_count") or 0) > 0
    )

    append_case = by_case["append_only_chain"]
    append_chain = _verify_chain(append_case.get("events") or [])
    before = append_case.get("chain_before") or {}
    after = append_case.get("chain_after") or {}
    append_only_valid = (
        append_chain
        and append_case.get("mutation_results")
        == {
            "update": "sqlite_integrity_error",
            "delete": "sqlite_integrity_error",
        }
        and before == after
        and before.get("chain_valid") is True
        and before.get("head_sha256")
        == append_case["events"][-1].get("event_sha256")
    )

    reset_case = by_case["reset_visibility"]
    reset_events = reset_case.get("events") or []
    reset_valid = (
        _verify_chain(reset_events)
        and [event.get("tool_name") for event in reset_events]
        == ["fixture.before", RESET_TOOL_NAME, "fixture.after"]
        and reset_case.get("visible_tool_names") == ["fixture.after"]
    )

    legacy_case = by_case["legacy_payload_hiding"]
    legacy_row = legacy_case.get("public_row") or {}
    legacy_marker = legacy_case.get("forbidden_public_substring")
    legacy_valid = (
        isinstance(legacy_marker, str)
        and legacy_marker not in json.dumps(legacy_row, ensure_ascii=False)
        and legacy_row.get("error_code") == "legacy_unverified"
        and legacy_row.get("integrity_valid") is False
        and json.loads(legacy_row.get("params_json") or "{}")
        == {"_legacy_payload": "hidden"}
        and json.loads(legacy_row.get("response_json") or "{}")
        == {"_legacy_payload": "hidden"}
    )

    storage_case = by_case["storage_isolation"]
    storage_isolation_valid = (
        storage_case.get("audit_tables") == ["tool_calls"]
        and storage_case.get("legacy_tables") == ["tool_calls", "user_memory"]
        and storage_case.get("legacy_sha256_before")
        == storage_case.get("legacy_sha256_after")
        and storage_case.get("legacy_marker_absent_from_audit_store") is True
    )

    metrics = {
        "case_count": len(raw_cases),
        "privacy_projection_rate": 1.0 if privacy_valid else 0.0,
        "chain_integrity_rate": 1.0 if privacy_chain and append_chain and reset_valid else 0.0,
        "append_only_enforcement_rate": 1.0 if append_only_valid else 0.0,
        "reset_visibility_rate": 1.0 if reset_valid else 0.0,
        "legacy_payload_hiding_rate": 1.0 if legacy_valid else 0.0,
        "storage_isolation_rate": 1.0 if storage_isolation_valid else 0.0,
    }
    if result.get("metrics") != metrics:
        raise ValueError("tool-audit metrics do not match raw cases")
    if any(value != 1.0 for key, value in metrics.items() if key != "case_count"):
        raise ValueError("tool-audit contract gate did not pass")
    return artifact


def _verify_chain(events: list[dict[str, Any]]) -> bool:
    if not events:
        return False
    previous: str | None = None
    for expected_sequence, event in enumerate(events, start=1):
        if set(event) != EVENT_FIELDS | {"event_sha256"}:
            return False
        body = {key: event[key] for key in EVENT_FIELDS}
        if (
            event.get("privacy_version") != VERSION
            or event.get("sequence") != expected_sequence
            or event.get("previous_event_sha256") != previous
            or event.get("event_sha256") != _canonical_sha256(body)
            or event.get("status") not in {"ok", "error"}
            or not isinstance(event.get("redaction_count"), int)
            or event.get("redaction_count") < 0
        ):
            return False
        previous = event["event_sha256"]
    return True
