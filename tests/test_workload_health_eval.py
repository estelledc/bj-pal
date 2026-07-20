from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.workload_health import (  # noqa: E402
    canonical_artifact_sha256,
    evaluate_workload_health,
    verify_workload_health_artifact,
)


def _artifact() -> dict:
    artifact = {
        "schema_version": 1,
        "evaluation": "durable-workload-health",
        "generated_at": "2026-07-20T00:00:00+00:00",
        "classification": "deterministic_synthetic_contract",
        "scope_warning": "synthetic workload fixture",
        "result": evaluate_workload_health(),
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


def test_workload_health_eval_recomputes_mixed_and_empty_windows(tmp_path: Path) -> None:
    path = tmp_path / "workload-health.json"
    _write(path, _artifact())

    artifact = verify_workload_health_artifact(path)

    assert artifact["result"]["case_count"] == 2
    assert artifact["result"]["metrics"] == {
        "aggregate_accuracy_rate": 1.0,
        "latency_accuracy_rate": 1.0,
        "integrity_rate": 1.0,
        "privacy_minimization_rate": 1.0,
        "empty_window_null_rate": 1,
    }


def test_verifier_rejects_rehashed_aggregate_and_quantile_tampering(
    tmp_path: Path,
) -> None:
    artifact = _artifact()
    path = tmp_path / "workload-health.json"
    observed = artifact["result"]["raw_cases"][0]["observed"]
    observed["terminal_success_rate"] = 1.0
    observed["artifact_sha256"] = _inner_sha(observed)
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    _write(path, artifact)

    with pytest.raises(ValueError, match="terminal_success_rate"):
        verify_workload_health_artifact(path)

    artifact = _artifact()
    observed = artifact["result"]["raw_cases"][0]["observed"]
    observed["queue_wait_ms"]["p95_ms"] = 1.0
    observed["artifact_sha256"] = _inner_sha(observed)
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    _write(path, artifact)

    with pytest.raises(ValueError, match="queue_wait_ms"):
        verify_workload_health_artifact(path)


def test_verifier_rejects_rehashed_identifier_reintroduction(tmp_path: Path) -> None:
    artifact = _artifact()
    path = tmp_path / "workload-health.json"
    observed = artifact["result"]["raw_cases"][0]["observed"]
    observed["job_id"] = "synthetic-job-private"
    observed["artifact_sha256"] = _inner_sha(observed)
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    _write(path, artifact)

    with pytest.raises(ValueError, match="private marker|forbidden key"):
        verify_workload_health_artifact(path)


def test_verifier_rejects_rehashed_nested_identifier_reintroduction(
    tmp_path: Path,
) -> None:
    artifact = _artifact()
    path = tmp_path / "workload-health.json"
    observed = artifact["result"]["raw_cases"][0]["observed"]
    observed["queue_wait_ms"]["request_id"] = "nested-marker"
    observed["artifact_sha256"] = _inner_sha(observed)
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    _write(path, artifact)

    with pytest.raises(ValueError, match="forbidden key"):
        verify_workload_health_artifact(path)
