"""Independent artifact coverage for v5.6 clarification continuation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.clarifications import (  # noqa: E402
    evaluate_clarification_continuations,
    load_golden_set,
)
from evals.clarifications.verify import (  # noqa: E402
    canonical_artifact_sha256,
    verify_clarification_artifact,
)


GOLDEN = ROOT / "evals" / "clarifications" / "golden.json"


def _artifact() -> dict:
    golden = load_golden_set(GOLDEN)
    artifact = {
        "schema_version": 1,
        "evaluation": golden.name,
        "generated_at": "2026-07-20T00:00:00+00:00",
        "classification": golden.classification,
        "scope_warning": "synthetic test fixture",
        "golden_set": {
            "sha256": golden.sha256,
            "case_count": len(golden.cases),
            "label_basis": golden.label_basis,
        },
        "result": evaluate_clarification_continuations(golden),
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    return artifact


def test_clarification_golden_set_meets_bounded_thresholds() -> None:
    artifact = _artifact()
    metrics = artifact["result"]["metrics"]

    assert artifact["result"]["case_count"] == 16
    assert metrics["one_step_resolution_success_rate"] == 1
    assert metrics["effective_value_accuracy_rate"] == 1
    assert metrics["same_conflict_recurrence_rate"] == 0
    assert metrics["decision_fingerprint_valid_rate"] == 1
    assert metrics["continuation_hash_chain_valid_rate"] == 1
    assert metrics["durable_restore_rate"] == 1
    assert metrics["resolution_round_trip_rate"] == 1
    assert metrics["alternate_resolution_conflict_detection_rate"] == 1


def test_clarification_verifier_rejects_metric_and_decision_tampering(
    tmp_path: Path,
) -> None:
    artifact = _artifact()
    artifact_path = tmp_path / "clarifications.json"
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")

    assert verify_clarification_artifact(artifact_path, GOLDEN)["result"]["case_count"] == 16

    artifact["result"]["raw_cases"][0]["decision_sha256"] = "0" * 64
    artifact["result"]["metrics"]["decision_fingerprint_valid_rate"] = 0
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="decision fingerprint"):
        verify_clarification_artifact(artifact_path, GOLDEN)
