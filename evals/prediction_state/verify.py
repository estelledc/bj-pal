from __future__ import annotations

import hashlib
from typing import Any

from storage.verified_copy import canonical_json, canonical_sha256


def _row_digest(rows: list[list[Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_json(row).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def verify_prediction_state(artifact: dict[str, Any]) -> dict[str, Any]:
    signed = dict(artifact)
    claimed_artifact_sha = signed.pop("artifact_sha256", None)
    if claimed_artifact_sha != canonical_sha256(signed):
        raise ValueError("artifact sha256 mismatch")
    cases = {case["case_id"]: case for case in artifact["result"]["raw_cases"]}
    if set(cases) != {
        "dry_run_read_only", "verified_copy", "post_migration_mutation",
        "wal_fail_closed",
    }:
        raise ValueError("unexpected prediction-state cases")

    dry = cases["dry_run_read_only"]
    preview = dict(dry["preview"])
    preview_sha = preview.pop("preview_sha256", None)
    dry_ok = (
        dry["source_sha256_before"] == dry["source_sha256_after"]
        and dry["destination_created"] is False
        and preview_sha == canonical_sha256(preview)
        and preview["mode"] == "dry_run"
    )

    copied = cases["verified_copy"]
    migration = dict(copied["migration"])
    migration_sha = migration.pop("migration_sha256", None)
    metadata = copied["metadata"]
    rows = copied["destination_rows"]
    copy_ok = (
        copied["source_sha256_before"] == copied["source_sha256_after"]
        and migration_sha == canonical_sha256(migration)
        and migration["source_counts"] == migration["destination_counts"]
        and migration["source_digests"] == migration["destination_digests"]
        and migration["destination_digests"]["prediction_log"] == _row_digest(rows)
        and rows[0][0] == 7 and rows[0][5] is None and rows[1][0] == 11
    )
    receipt_ok = metadata["receipt_sha256"] == canonical_sha256(metadata["body"])
    isolation_ok = (
        copied["destination_tables"] == ["prediction_log", "state_store_metadata"]
        and copied["private_marker_absent"] is True
    )

    mutation = cases["post_migration_mutation"]
    mutation_ok = (
        mutation["actual_updated"] is True
        and mutation["history_deleted_count"] == 1
        and mutation["remaining_rows"] == [[7, "poi-a", 42]]
        and mutation["source_sha256_before"] == mutation["source_sha256_after"]
    )
    wal = cases["wal_fail_closed"]
    wal_ok = (
        wal["error_code"] == "wal_source_rejected"
        and wal["destination_created"] is False
    )
    metrics = {
        "case_count": 4,
        "dry_run_read_only_rate": float(dry_ok),
        "source_preservation_rate": float(
            dry_ok
            and copied["source_sha256_before"] == copied["source_sha256_after"]
            and mutation["source_sha256_before"] == mutation["source_sha256_after"]
        ),
        "copy_integrity_rate": float(copy_ok),
        "receipt_integrity_rate": float(receipt_ok),
        "domain_isolation_rate": float(isolation_ok),
        "mutable_continuation_rate": float(mutation_ok),
        "wal_fail_closed_rate": float(wal_ok),
    }
    if artifact["result"]["metrics"] != metrics or any(
        value != 1.0 for key, value in metrics.items() if key != "case_count"
    ):
        raise ValueError("prediction-state metrics mismatch")
    return metrics
