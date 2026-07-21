from __future__ import annotations

import copy

import pytest

from evals.prediction_state.evaluate import evaluate_prediction_state
from evals.prediction_state.verify import verify_prediction_state
from storage.verified_copy import canonical_sha256


def _resign(artifact: dict) -> None:
    artifact.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = canonical_sha256(artifact)


def test_prediction_state_artifact_verifies() -> None:
    artifact = evaluate_prediction_state()
    assert verify_prediction_state(artifact)["mutable_continuation_rate"] == 1.0


def test_prediction_state_verifier_rejects_source_tamper() -> None:
    artifact = evaluate_prediction_state()
    tampered = copy.deepcopy(artifact)
    tampered["result"]["raw_cases"][1]["source_sha256_after"] = "0" * 64
    _resign(tampered)
    with pytest.raises(ValueError, match="metrics mismatch"):
        verify_prediction_state(tampered)


def test_prediction_state_verifier_rejects_mutation_tamper() -> None:
    artifact = evaluate_prediction_state()
    tampered = copy.deepcopy(artifact)
    tampered["result"]["raw_cases"][2]["remaining_rows"] = []
    _resign(tampered)
    with pytest.raises(ValueError, match="metrics mismatch"):
        verify_prediction_state(tampered)
