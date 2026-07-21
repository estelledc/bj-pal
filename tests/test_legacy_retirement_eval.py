from __future__ import annotations

import copy

import pytest

from evals.legacy_retirement.evaluate import evaluate_legacy_retirement
from evals.legacy_retirement.verify import verify_legacy_retirement
from storage.verified_copy import canonical_sha256


def _resign(artifact: dict) -> None:
    artifact.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = canonical_sha256(artifact)


def test_legacy_retirement_artifact_verifies() -> None:
    metrics = verify_legacy_retirement(evaluate_legacy_retirement())
    assert metrics["source_drift_detection_rate"] == 1.0


def test_legacy_retirement_verifier_rejects_drift_tamper() -> None:
    artifact = evaluate_legacy_retirement()
    tampered = copy.deepcopy(artifact)
    tampered["result"]["raw_cases"][1]["audit"]["checks"][
        "prediction_feedback_legacy_binding"
    ] = "ok"
    _resign(tampered)
    with pytest.raises(ValueError, match="metrics mismatch"):
        verify_legacy_retirement(tampered)


def test_legacy_retirement_verifier_rejects_payload_marker() -> None:
    artifact = evaluate_legacy_retirement()
    tampered = copy.deepcopy(artifact)
    tampered["result"]["raw_cases"][0]["audit"]["payload"] = (
        "private-legacy-payload-marker"
    )
    _resign(tampered)
    with pytest.raises(ValueError, match="metrics mismatch"):
        verify_legacy_retirement(tampered)
