"""Integrity and semantics of v5.5 Constraint Ledger evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.constraints import evaluate_constraint_ledger, load_golden_set  # noqa: E402
from evals.constraints.verify import (  # noqa: E402
    canonical_artifact_sha256,
    verify_constraint_artifact,
)


GOLDEN = ROOT / "evals" / "constraints" / "golden.json"


def test_project_constraint_golden_set_is_nontrivial_and_has_negatives() -> None:
    golden = load_golden_set(GOLDEN)

    assert len(golden.cases) >= 25
    assert golden.classification == "synthetic"
    assert sum(not case.expected_text for case in golden.cases) >= 6
    assert sum(bool(case.expected_conflicts) for case in golden.cases) >= 5
    assert any(len(case.expected_text) >= 5 for case in golden.cases)


def test_constraint_metrics_cover_extraction_preservation_conflicts_and_idempotency() -> None:
    result = evaluate_constraint_ledger(load_golden_set(GOLDEN))
    metrics = result["metrics"]

    assert result["case_count"] >= 25
    assert metrics == {
        "field_extraction_precision": 1.0,
        "field_extraction_recall": 1.0,
        "field_extraction_f1": 1.0,
        "false_extraction_rate": 0.0,
        "hard_constraint_preservation_rate": 1.0,
        "explicit_conflict_detection_recall": 1.0,
        "false_conflict_rate": 0.0,
        "rewrite_constraint_coverage_rate": 1.0,
        "round_trip_idempotency_rate": 1.0,
    }


def test_constraint_artifact_verifier_rejects_resealed_raw_case_tampering(
    tmp_path: Path,
) -> None:
    golden = load_golden_set(GOLDEN)
    artifact = {
        "schema_version": 1,
        "evaluation": golden.name,
        "generated_at": "2026-07-20T00:00:00+00:00",
        "classification": golden.classification,
        "scope_warning": "synthetic fixture",
        "golden_set": {
            "sha256": golden.sha256,
            "case_count": len(golden.cases),
            "label_basis": golden.label_basis,
        },
        "result": evaluate_constraint_ledger(golden),
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    path = tmp_path / "constraints.json"
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    verify_constraint_artifact(path, GOLDEN)

    artifact["result"]["raw_cases"][0]["observed_text"][
        "preferences.party_size"
    ] = 99
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="observed text"):
        verify_constraint_artifact(path, GOLDEN)
