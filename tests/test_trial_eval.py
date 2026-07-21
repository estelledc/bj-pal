from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evals.trials.evaluate import evaluate_trials, write_artifact  # noqa: E402
from evals.trials.verify import verify_trial_artifact  # noqa: E402


def _sha(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _reseal(artifact: dict) -> None:
    artifact.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = _sha(artifact)


def test_trial_evaluation_round_trip_and_all_contract_metrics(tmp_path) -> None:
    artifact = evaluate_trials()
    path = tmp_path / "trial-evidence.json"
    write_artifact(path, artifact)

    verified = verify_trial_artifact(path)
    metrics = verified["result"]["metrics"]
    assert metrics["case_count"] == 6
    assert all(value == 1.0 for key, value in metrics.items() if key != "case_count")


def test_trial_verifier_rejects_resigned_privacy_claim(tmp_path) -> None:
    artifact = evaluate_trials()
    tampered = deepcopy(artifact)
    consent = next(
        case
        for case in tampered["result"]["raw_cases"]
        if case["case_id"] == "consent_and_capability_binding"
    )
    consent["raw_capabilities_persisted"] = True
    _reseal(tampered)
    path = tmp_path / "tampered-privacy.json"
    write_artifact(path, tampered)

    with pytest.raises(ValueError, match="metrics mismatch"):
        verify_trial_artifact(path)


def test_trial_verifier_rejects_resigned_fabricated_snapshot_rate(tmp_path) -> None:
    artifact = evaluate_trials()
    tampered = deepcopy(artifact)
    minimum = next(
        case
        for case in tampered["result"]["raw_cases"]
        if case["case_id"] == "minimum_gate_and_snapshot_integrity"
    )
    snapshot = minimum["snapshot"]
    snapshot["outcome_completion_rate"] = 1.0
    snapshot.pop("snapshot_sha256")
    snapshot["snapshot_sha256"] = _sha(snapshot)
    _reseal(tampered)
    path = tmp_path / "tampered-rate.json"
    write_artifact(path, tampered)

    with pytest.raises(ValueError, match="metrics mismatch"):
        verify_trial_artifact(path)


def test_trial_verifier_rejects_resigned_raw_participant_hash(tmp_path) -> None:
    artifact = evaluate_trials()
    tampered = deepcopy(artifact)
    minimum = next(
        case
        for case in tampered["result"]["raw_cases"]
        if case["case_id"] == "minimum_gate_and_snapshot_integrity"
    )
    minimum["participant_evidence"][0]["consented_at"] = "tampered"
    _reseal(tampered)
    path = tmp_path / "tampered-participant.json"
    write_artifact(path, tampered)

    with pytest.raises(ValueError, match="participant evidence SHA-256 mismatch"):
        verify_trial_artifact(path)
