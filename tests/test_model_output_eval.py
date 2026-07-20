from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.model_output.evaluate import evaluate_model_output  # noqa: E402
from evals.model_output.verify import verify_model_output_artifact  # noqa: E402


def _sha(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _write(path: Path, artifact: dict) -> None:
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")


def _rehash_artifact(artifact: dict) -> None:
    canonical = deepcopy(artifact)
    canonical.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = _sha(canonical)


def test_model_output_artifact_recomputes_contract_and_lifecycle(tmp_path: Path) -> None:
    artifact = evaluate_model_output()
    path = tmp_path / "model-output.json"
    _write(path, artifact)

    verified = verify_model_output_artifact(path)
    metrics = verified["result"]["metrics"]
    assert metrics["contract_case_count"] == 13
    assert metrics["decision_accuracy_rate"] == 1.0
    assert metrics["expected_issue_detection_rate"] == 1.0
    assert metrics["valid_false_rejection_rate"] == 0.0
    assert metrics["schema_rejection_rate"] == 1.0
    assert metrics["grounding_rejection_rate"] == 1.0
    assert metrics["sequence_rejection_rate"] == 1.0
    assert metrics["first_pass_single_call_rate"] == 1.0
    assert metrics["bounded_repair_success_rate"] == 1.0
    assert metrics["repair_exhaustion_fail_closed_rate"] == 1.0
    assert metrics["repair_budget_enforcement_rate"] == 1.0
    assert metrics["privacy_marker_exclusion_rate"] == 1.0


def test_model_output_verifier_rejects_self_rehashed_false_issue_code(
    tmp_path: Path,
) -> None:
    tampered = deepcopy(evaluate_model_output())
    case = next(
        item
        for item in tampered["result"]["contract_cases"]
        if item["case_id"] == "candidate_id_hallucination"
    )
    case["observed_issue_codes"] = []
    case["observed_status"] = "accepted"
    _rehash_artifact(tampered)
    path = tmp_path / "tampered-issue.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="independent status mismatch"):
        verify_model_output_artifact(path)


def test_model_output_verifier_rejects_self_rehashed_false_call_count(
    tmp_path: Path,
) -> None:
    tampered = deepcopy(evaluate_model_output())
    case = next(
        item
        for item in tampered["result"]["lifecycle_cases"]
        if item["case_id"] == "bounded_repair_success"
    )
    case["client_body_count"] = 1
    _rehash_artifact(tampered)
    path = tmp_path / "tampered-call-count.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="bounded-repair lifecycle accounting"):
        verify_model_output_artifact(path)
