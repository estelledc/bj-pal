from __future__ import annotations

import copy

import pytest

from evals.user_memory_state.evaluate import evaluate_user_memory_state
from evals.user_memory_state.verify import verify_user_memory_state
from storage.verified_copy import canonical_sha256


def _resign(artifact: dict) -> None:
    artifact.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = canonical_sha256(artifact)


def test_user_memory_state_artifact_verifies() -> None:
    artifact = evaluate_user_memory_state()
    metrics = verify_user_memory_state(artifact)
    assert metrics["privacy_delete_rate"] == 1.0


def test_user_memory_state_verifier_rejects_pair_digest_tamper() -> None:
    artifact = evaluate_user_memory_state()
    tampered = copy.deepcopy(artifact)
    tampered["result"]["raw_cases"][1]["migration"]["destination_digests"][
        "user_memory_events"
    ] = "0" * 64
    migration = tampered["result"]["raw_cases"][1]["migration"]
    migration.pop("migration_sha256", None)
    migration["migration_sha256"] = canonical_sha256(migration)
    _resign(tampered)
    with pytest.raises(ValueError, match="metrics mismatch"):
        verify_user_memory_state(tampered)


def test_user_memory_state_verifier_rejects_privacy_delete_tamper() -> None:
    artifact = evaluate_user_memory_state()
    tampered = copy.deepcopy(artifact)
    tampered["result"]["raw_cases"][2]["deleted_state_count"] = 0
    _resign(tampered)
    with pytest.raises(ValueError, match="metrics mismatch"):
        verify_user_memory_state(tampered)
