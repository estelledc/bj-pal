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

from evals.outcomes.evaluate import evaluate_outcomes  # noqa: E402
from evals.outcomes.verify import verify_outcome_artifact  # noqa: E402


def _sha(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write(path: Path, artifact: dict) -> None:
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")


def _resign(artifact: dict) -> None:
    payload = deepcopy(artifact)
    payload.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = _sha(payload)


def test_outcome_artifact_recomputes_every_metric(tmp_path: Path) -> None:
    artifact = evaluate_outcomes()
    path = tmp_path / "outcomes.json"
    _write(path, artifact)

    verified = verify_outcome_artifact(path)

    assert verified["result"]["metrics"] == {
        "case_count": 4,
        "capability_binding_rate": 1.0,
        "artifact_integrity_rate": 1.0,
        "idempotency_rate": 1.0,
        "schema_validation_rate": 1.0,
        "expiry_fail_closed_rate": 1.0,
        "append_only_rate": 1.0,
        "privacy_minimization_rate": 1.0,
        "minimum_sample_gate_rate": 1.0,
    }


def test_outcome_verifier_rejects_rehashed_fabricated_report(tmp_path: Path) -> None:
    artifact = evaluate_outcomes()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "capability_and_artifact_binding"
    )
    case["report"]["value"] = "rejected"
    _resign(tampered)
    path = tmp_path / "fabricated-report.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_outcome_artifact(path)

def test_outcome_verifier_rejects_claimed_small_sample_rate(tmp_path: Path) -> None:
    artifact = evaluate_outcomes()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "minimum_sample_gate"
    )
    case["before"]["outcome_completion_rate"] = 1.0
    _resign(tampered)
    path = tmp_path / "fabricated-rate.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_outcome_artifact(path)
