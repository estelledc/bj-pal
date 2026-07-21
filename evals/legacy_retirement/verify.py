from __future__ import annotations

import json
from typing import Any

from storage.verified_copy import canonical_sha256


PRIVATE_MARKERS = (
    "private-legacy-payload-marker",
    "private-unknown-marker",
    '"北京"',
)


def verify_legacy_retirement(artifact: dict[str, Any]) -> dict[str, Any]:
    signed = dict(artifact)
    claimed_sha = signed.pop("artifact_sha256", None)
    if claimed_sha != canonical_sha256(signed):
        raise ValueError("artifact sha256 mismatch")
    cases = {case["case_id"]: case["audit"] for case in artifact["result"]["raw_cases"]}
    if set(cases) != {
        "verified_owners",
        "source_drift",
        "unknown_table",
        "missing_receipts",
    }:
        raise ValueError("unexpected legacy-retirement cases")

    ready = cases["verified_owners"]
    expected_counts = {
        "plan_outcome": 1,
        "plan_trace": 1,
        "prediction_log": 1,
        "tool_calls": 1,
        "user_memory": 1,
        "user_memory_events": 1,
    }
    ready_ok = (
        ready["ready"] is True
        and ready["policy"] == "dedicated_required"
        and ready["legacy_counts"] == expected_counts
        and all(value == "ok" for value in ready["checks"].values())
        and set(ready["resolved_database_names"])
        == {"plan_evidence", "prediction_feedback", "user_memory", "tool_audit"}
    )

    drift = cases["source_drift"]
    drift_ok = (
        drift["ready"] is False
        and drift["checks"]["prediction_feedback_legacy_binding"] == "source_drift"
    )
    unknown = cases["unknown_table"]
    unknown_ok = (
        unknown["ready"] is False
        and unknown["checks"]["legacy_known_tables"] == "unknown:mystery_state"
    )
    fallback = cases["missing_receipts"]
    fallback_ok = fallback["ready"] is False and all(
        fallback["checks"][f"{domain}_dedicated"] == "legacy_fallback"
        for domain in ("plan_evidence", "prediction_feedback", "user_memory")
    )
    rendered = json.dumps(artifact, ensure_ascii=False, sort_keys=True)
    payload_ok = all(marker not in rendered for marker in PRIVATE_MARKERS)

    metrics = {
        "case_count": 4,
        "verified_owner_acceptance_rate": float(ready_ok),
        "source_drift_detection_rate": float(drift_ok),
        "unknown_table_detection_rate": float(unknown_ok),
        "missing_receipt_detection_rate": float(fallback_ok),
        "payload_exclusion_rate": float(payload_ok),
    }
    if artifact["result"]["metrics"] != metrics or any(
        value != 1.0 for key, value in metrics.items() if key != "case_count"
    ):
        raise ValueError("legacy-retirement metrics mismatch")
    return metrics
