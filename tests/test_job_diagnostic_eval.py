"""Independent artifact checks for v6.19 durable-job diagnosis."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.job_diagnostics import (  # noqa: E402
    canonical_artifact_sha256,
    evaluate_job_diagnostics,
    verify_job_diagnostic_artifact,
)


def _artifact() -> dict:
    artifact = {
        "schema_version": 1,
        "evaluation": "durable-job-incident-diagnosis",
        "generated_at": "2026-07-20T00:00:00+00:00",
        "classification": "deterministic_synthetic_contract",
        "scope_warning": "synthetic diagnostic fixture",
        "result": evaluate_job_diagnostics(),
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    return artifact


def _write(path: Path, artifact: dict) -> None:
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")


def _inner_sha(payload: dict) -> str:
    unsigned = dict(payload)
    unsigned.pop("artifact_sha256", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_job_diagnostic_eval_covers_every_class_with_bounded_claims(tmp_path: Path) -> None:
    artifact = _artifact()
    path = tmp_path / "job-diagnostics.json"
    _write(path, artifact)

    verified = verify_job_diagnostic_artifact(path)
    metrics = verified["result"]["metrics"]

    assert verified["result"]["case_count"] == 14
    assert metrics == {
        "classification_accuracy_rate": 1.0,
        "recommended_action_accuracy_rate": 1.0,
        "classification_coverage_count": 14,
        "integrity_rate": 1.0,
        "privacy_minimization_rate": 1.0,
        "unknown_error_non_promotion_rate": 1,
    }


def test_verifier_rejects_rehashed_classification_and_event_chain_tampering(
    tmp_path: Path,
) -> None:
    artifact = _artifact()
    path = tmp_path / "job-diagnostics.json"
    case = artifact["result"]["raw_cases"][0]
    case["observed"]["classification"] = "completed"
    case["observed"]["artifact_sha256"] = _inner_sha(case["observed"])
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    _write(path, artifact)

    with pytest.raises(ValueError, match="classification"):
        verify_job_diagnostic_artifact(path)

    artifact = _artifact()
    case = artifact["result"]["raw_cases"][1]
    case["observed"]["event_sequence_sha256"] = "0" * 64
    case["observed"]["artifact_sha256"] = _inner_sha(case["observed"])
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    _write(path, artifact)

    with pytest.raises(ValueError, match="event chain"):
        verify_job_diagnostic_artifact(path)


def test_verifier_rejects_rehashed_private_payload_reintroduction(tmp_path: Path) -> None:
    artifact = _artifact()
    path = tmp_path / "job-diagnostics.json"
    case = artifact["result"]["raw_cases"][2]
    case["observed"]["payload"] = {"provider_error": "private-secret-marker"}
    case["observed"]["artifact_sha256"] = _inner_sha(case["observed"])
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    _write(path, artifact)

    with pytest.raises(ValueError, match="private marker|forbidden keys"):
        verify_job_diagnostic_artifact(path)
