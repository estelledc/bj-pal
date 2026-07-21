"""Integrity and semantics of the requirement-gate evaluation evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.requirements import evaluate_requirement_gate, load_golden_set  # noqa: E402
from evals.requirements.verify import (  # noqa: E402
    canonical_artifact_sha256,
    verify_requirement_artifact,
)


GOLDEN = ROOT / "evals" / "requirements" / "golden.json"


def test_project_requirement_golden_set_is_nontrivial_and_balanced() -> None:
    golden = load_golden_set(GOLDEN)

    assert len(golden.cases) >= 18
    assert golden.classification == "synthetic"
    statuses = {case.expected_status for case in golden.cases}
    assert statuses == {
        "proceed",
        "proceed_with_assumptions",
        "clarification_required",
    }
    assert sum(case.follow_up is not None for case in golden.cases) >= 6


def test_requirement_metrics_cover_trigger_false_positive_and_follow_up() -> None:
    result = evaluate_requirement_gate(load_golden_set(GOLDEN))
    metrics = result["metrics"]

    assert result["case_count"] >= 18
    assert 0 < metrics["clarification_trigger_rate"] < 1
    assert metrics["false_clarification_rate"] == 0
    assert metrics["required_clarification_recall"] == 1
    assert metrics["decision_accuracy"] == 1
    assert metrics["post_clarification_gate_executability_rate"] == 1


def test_requirement_artifact_verifier_rejects_tampered_metrics(tmp_path: Path) -> None:
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
        "result": evaluate_requirement_gate(golden),
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    path = tmp_path / "requirements.json"
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    verify_requirement_artifact(path, GOLDEN)

    artifact["result"]["metrics"]["false_clarification_rate"] = 1
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="metrics"):
        verify_requirement_artifact(path, GOLDEN)
