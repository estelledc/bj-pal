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


def _expected_digests() -> dict[str, str]:
    memory_rows = [
        [7, "user-a", "fact", "area:city", '"北京"', 0.8, 1, 1.0, 2.0,
         0, "explicit_user_input", 2.0, None, 1],
        [11, "user-b", "preference", "taste:coffee", "true", 0.7, 2,
         3.0, 4.0, 1, "manual_entry", 3.0, None, 1],
    ]
    event_rows = [
        [13, "user-a", "fact", "area:city", "created", 1,
         "explicit_user_input", "a" * 64, None, "new_memory", 2.0],
        [21, "user-b", "preference", "taste:coffee", "forgotten", 1,
         "manual_entry", "b" * 64, None, "user_soft_forget", 4.0],
    ]
    return {
        "user_memory": _row_digest(memory_rows),
        "user_memory_events": _row_digest(event_rows),
    }


def verify_user_memory_state(artifact: dict[str, Any]) -> dict[str, Any]:
    signed = dict(artifact)
    claimed_artifact_sha = signed.pop("artifact_sha256", None)
    if claimed_artifact_sha != canonical_sha256(signed):
        raise ValueError("artifact sha256 mismatch")
    cases = {case["case_id"]: case for case in artifact["result"]["raw_cases"]}
    if set(cases) != {
        "dry_run_read_only",
        "verified_pair_copy",
        "post_migration_lifecycle",
        "wal_fail_closed",
    }:
        raise ValueError("unexpected user-memory-state cases")

    dry = cases["dry_run_read_only"]
    preview = dict(dry["preview"])
    preview_sha = preview.pop("preview_sha256", None)
    dry_ok = (
        dry["source_sha256_before"] == dry["source_sha256_after"]
        and dry["destination_created"] is False
        and preview_sha == canonical_sha256(preview)
        and preview["mode"] == "dry_run"
        and preview["source_counts"]
        == {"user_memory": 2, "user_memory_events": 2}
    )

    copied = cases["verified_pair_copy"]
    migration = dict(copied["migration"])
    migration_sha = migration.pop("migration_sha256", None)
    expected_digests = _expected_digests()
    copy_ok = (
        copied["source_sha256_before"] == copied["source_sha256_after"]
        and migration_sha == canonical_sha256(migration)
        and migration["source_counts"] == migration["destination_counts"]
        and migration["source_digests"] == migration["destination_digests"]
        and migration["destination_digests"] == expected_digests
        and copied["state_projection"] == [[7, 0, 1, 1], [11, 1, 1, 1]]
        and copied["event_projection"]
        == [[13, "created", 1], [21, "forgotten", 1]]
    )
    metadata = copied["metadata"]
    receipt_ok = metadata["receipt_sha256"] == canonical_sha256(metadata["body"])
    isolation_ok = (
        copied["destination_tables"]
        == ["state_store_metadata", "user_memory", "user_memory_events"]
        and copied["private_marker_absent"] is True
    )

    lifecycle = cases["post_migration_lifecycle"]
    expected_value_hash = hashlib.sha256('"上海"'.encode("utf-8")).hexdigest()
    mutation_ok = (
        lifecycle["replace_action"] == "replaced"
        and lifecycle["remaining_state"] == [[7, 2, "user-a", expected_value_hash]]
        and lifecycle["remaining_events"][-1][1:] == ["user-a", "replaced", 2]
        and lifecycle["source_sha256_before"] == lifecycle["source_sha256_after"]
    )
    privacy_delete_ok = (
        lifecycle["deleted_state_count"] == 1
        and all(row[1] != "user-b" for row in lifecycle["remaining_events"])
    )
    immutable_ok = lifecycle["event_immutability"] == "event_update_rejected"
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
            and lifecycle["source_sha256_before"] == lifecycle["source_sha256_after"]
        ),
        "pair_copy_integrity_rate": float(copy_ok),
        "receipt_integrity_rate": float(receipt_ok),
        "domain_isolation_rate": float(isolation_ok),
        "mutable_continuation_rate": float(mutation_ok),
        "privacy_delete_rate": float(privacy_delete_ok),
        "event_immutability_rate": float(immutable_ok),
        "wal_fail_closed_rate": float(wal_ok),
    }
    if artifact["result"]["metrics"] != metrics or any(
        value != 1.0 for key, value in metrics.items() if key != "case_count"
    ):
        raise ValueError("user-memory-state metrics mismatch")
    return metrics
