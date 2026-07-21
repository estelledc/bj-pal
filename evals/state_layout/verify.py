"""Independently verify state-layout migration evidence."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def _sha(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def verify_state_layout_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    canonical = deepcopy(artifact)
    observed_sha = canonical.pop("artifact_sha256", None)
    if observed_sha != _sha(canonical):
        raise ValueError("state-layout artifact SHA-256 mismatch")
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported state-layout artifact schema")
    if artifact.get("classification") != "synthetic_contract":
        raise ValueError("state-layout classification mismatch")
    if artifact.get("policy") != {
        "domain": "plan_evidence",
        "layout_version": "state_layout_v1",
        "migration": "explicit_non_destructive_copy",
        "legacy_delete": "forbidden",
        "wal_source": "fail_closed",
    }:
        raise ValueError("state-layout policy mismatch")

    raw_cases = (artifact.get("result") or {}).get("raw_cases") or []
    by_case = {case.get("case_id"): case for case in raw_cases}
    if set(by_case) != {
        "dry_run_read_only",
        "verified_copy",
        "legacy_classification",
    } or len(by_case) != len(raw_cases):
        raise ValueError("state-layout case contract mismatch")

    dry = by_case["dry_run_read_only"]
    preview = dry.get("preview") or {}
    preview_body = deepcopy(preview)
    preview_sha = preview_body.pop("preview_sha256", None)
    dry_valid = (
        dry.get("source_file_sha256_before") == dry.get("source_file_sha256_after")
        and dry.get("destination_created") is False
        and preview.get("mode") == "dry_run"
        and preview.get("legacy_source_modified") is False
        and preview_sha == _sha(preview_body)
    )

    copied = by_case["verified_copy"]
    migration = copied.get("migration") or {}
    metadata = copied.get("metadata") or {}
    metadata_body = metadata.get("body") or {}
    migration_body = deepcopy(migration)
    migration_sha = migration_body.pop("migration_sha256", None)
    source_preserved = (
        copied.get("source_file_sha256_before")
        == copied.get("source_file_sha256_after")
        and migration.get("legacy_source_modified") is False
    )
    copy_valid = (
        migration.get("source_counts") == migration.get("destination_counts")
        and migration.get("source_digests") == migration.get("destination_digests")
        and migration.get("destination_quick_check") == "ok"
        and migration.get("receipt_valid") is True
        and migration_sha == _sha(migration_body)
    )
    receipt_valid = (
        metadata_body.get("domain") == "plan_evidence"
        and metadata_body.get("layout_version") == "state_layout_v1"
        and metadata_body.get("origin") == "migrated_copy"
        and metadata.get("receipt_sha256") == _sha(metadata_body)
        and metadata_body.get("source_counts") == migration.get("source_counts")
        and metadata_body.get("destination_digests")
        == migration.get("destination_digests")
    )
    isolation_valid = (
        copied.get("destination_tables")
        == ["plan_outcome", "plan_trace", "state_store_metadata"]
        and copied.get("private_markers_absent") is True
    )

    legacy = by_case["legacy_classification"]
    legacy_migration = legacy.get("migration") or {}
    legacy_migration_body = deepcopy(legacy_migration)
    legacy_migration_sha = legacy_migration_body.pop("migration_sha256", None)
    legacy_valid = (
        legacy.get("outcome_classification") == "legacy_unclassified"
        and legacy_migration.get("receipt_valid") is True
        and legacy_migration.get("source_counts")
        == legacy_migration.get("destination_counts")
        and legacy_migration_sha == _sha(legacy_migration_body)
    )

    metrics = {
        "case_count": len(raw_cases),
        "dry_run_read_only_rate": 1.0 if dry_valid else 0.0,
        "source_preservation_rate": 1.0 if source_preserved else 0.0,
        "copy_integrity_rate": 1.0 if copy_valid else 0.0,
        "receipt_integrity_rate": 1.0 if receipt_valid else 0.0,
        "domain_isolation_rate": 1.0 if isolation_valid else 0.0,
        "legacy_classification_rate": 1.0 if legacy_valid else 0.0,
    }
    if (artifact.get("result") or {}).get("metrics") != metrics:
        raise ValueError("state-layout metrics do not match raw cases")
    if any(value != 1.0 for key, value in metrics.items() if key != "case_count"):
        raise ValueError("state-layout contract gate did not pass")
    return artifact
