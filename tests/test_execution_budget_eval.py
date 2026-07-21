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

from evals.execution_budget.evaluate import evaluate_execution_budget  # noqa: E402
from evals.execution_budget.verify import verify_execution_budget_artifact  # noqa: E402


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


def test_execution_budget_artifact_recomputes_all_metrics(tmp_path: Path) -> None:
    artifact = evaluate_execution_budget()
    path = tmp_path / "execution-budget.json"
    _write(path, artifact)

    verified = verify_execution_budget_artifact(path)
    assert verified["classification"] == "synthetic_contract"
    assert verified["result"]["metrics"] == {
        "case_count": 6,
        "snapshot_integrity_rate": 1.0,
        "termination_semantics_rate": 1.0,
        "post_limit_work_blocked_rate": 1.0,
        "privacy_marker_exclusion_rate": 1.0,
    }


def test_execution_budget_verifier_rejects_self_rehashed_false_limit(
    tmp_path: Path,
) -> None:
    artifact = evaluate_execution_budget()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "llm_n_plus_one_blocked"
    )
    case["snapshot"]["usage"]["llm_call_count"] = 1
    snapshot_payload = deepcopy(case["snapshot"])
    snapshot_payload.pop("artifact_sha256")
    case["snapshot"]["artifact_sha256"] = _sha(snapshot_payload)
    artifact_payload = deepcopy(tampered)
    artifact_payload.pop("artifact_sha256")
    tampered["artifact_sha256"] = _sha(artifact_payload)
    path = tmp_path / "tampered-budget.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_execution_budget_artifact(path)
