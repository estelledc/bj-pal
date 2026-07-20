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

from evals.orchestration.evaluate import evaluate_orchestration  # noqa: E402
from evals.orchestration.verify import verify_orchestration_artifact  # noqa: E402


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


def test_orchestration_artifact_recomputes_tradeoff_and_decision(tmp_path: Path) -> None:
    artifact = evaluate_orchestration()
    path = tmp_path / "orchestration.json"
    _write(path, artifact)

    verified = verify_orchestration_artifact(path)
    metrics = verified["result"]["metrics"]
    assert metrics["case_count"] == 3
    assert metrics["multi_quality_improvement_rate"] == 0.0
    assert metrics["constraint_non_regression_rate"] == 1.0
    assert metrics["semantic_output_change_rate"] == 0.0
    assert metrics["llm_call_multiplier"] == 3.0
    assert metrics["data_batch_multiplier"] == 3.0
    assert metrics["injected_branch_failure_containment_rate"] == 1.0
    assert metrics["default_budget_rejection_rate"] == 1.0
    assert verified["result"]["decision"] == "single_branch_default"


def test_orchestration_verifier_rejects_self_rehashed_false_call_count(
    tmp_path: Path,
) -> None:
    artifact = evaluate_orchestration()
    tampered = deepcopy(artifact)
    mode = tampered["result"]["raw_cases"][0]["multi"]
    mode["execution_budget"]["usage"]["llm_call_count"] = 1
    snapshot_payload = deepcopy(mode["execution_budget"])
    snapshot_payload.pop("artifact_sha256")
    mode["execution_budget"]["artifact_sha256"] = _sha(snapshot_payload)
    artifact_payload = deepcopy(tampered)
    artifact_payload.pop("artifact_sha256")
    tampered["artifact_sha256"] = _sha(artifact_payload)
    path = tmp_path / "tampered-orchestration.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="LLM call count"):
        verify_orchestration_artifact(path)
