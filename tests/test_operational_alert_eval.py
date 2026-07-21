from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.operational_alerts import (  # noqa: E402
    canonical_artifact_sha256,
    evaluate_operational_alerts,
    verify_operational_alert_artifact,
)


def _canonical_sha(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _artifact() -> dict:
    artifact = {
        "schema_version": 1,
        "evaluation": "operational-alert-contract",
        "generated_at": "2026-07-20T00:00:00+00:00",
        "classification": "deterministic_synthetic_contract",
        "scope_warning": "synthetic alert fixture",
        "result": evaluate_operational_alerts(),
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    return artifact


def _write(path: Path, artifact: dict) -> None:
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")


def _resign_snapshot(snapshot: dict) -> None:
    unsigned = dict(snapshot)
    unsigned.pop("artifact_sha256", None)
    snapshot["artifact_sha256"] = _canonical_sha(unsigned)


def _resign_workload(workload: dict) -> None:
    unsigned = dict(workload)
    unsigned.pop("artifact_sha256", None)
    workload["artifact_sha256"] = _canonical_sha(unsigned)


def _resign_outer(artifact: dict) -> None:
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)


def test_verifier_independently_recomputes_all_fixed_cases(tmp_path: Path) -> None:
    path = tmp_path / "operational-alerts.json"
    _write(path, _artifact())

    artifact = verify_operational_alert_artifact(path)

    assert artifact["result"]["case_count"] == 4
    assert artifact["result"]["metrics"] == {
        "decision_accuracy_rate": 1.0,
        "sample_gate_accuracy_rate": 1.0,
        "integrity_rate": 1.0,
        "source_binding_rate": 1.0,
        "privacy_minimization_rate": 1.0,
    }


def test_verifier_rejects_rehashed_rule_and_policy_tampering(tmp_path: Path) -> None:
    artifact = _artifact()
    path = tmp_path / "operational-alerts.json"
    snapshot = artifact["result"]["raw_cases"][0]["observed"]
    snapshot["rules"][0]["state"] = "firing"
    _resign_snapshot(snapshot)
    _resign_outer(artifact)
    _write(path, artifact)

    with pytest.raises(ValueError, match="rule decision"):
        verify_operational_alert_artifact(path)

    artifact = _artifact()
    snapshot = artifact["result"]["raw_cases"][0]["observed"]
    snapshot["policy"]["minimum_jobs"] = 1
    _resign_snapshot(snapshot)
    _resign_outer(artifact)
    _write(path, artifact)

    with pytest.raises(ValueError, match="policy drift"):
        verify_operational_alert_artifact(path)


def test_verifier_rejects_rehashed_workload_semantic_drift(tmp_path: Path) -> None:
    artifact = _artifact()
    path = tmp_path / "operational-alerts.json"
    case = artifact["result"]["raw_cases"][0]
    workload = case["source"]["workload"]
    workload["terminal_failure_rate"] = 0.9
    _resign_workload(workload)
    case["observed"]["workload_artifact_sha256"] = workload["artifact_sha256"]
    _resign_snapshot(case["observed"])
    _resign_outer(artifact)
    _write(path, artifact)

    with pytest.raises(ValueError, match="terminal_failure_rate"):
        verify_operational_alert_artifact(path)


def test_verifier_rejects_rehashed_identifier_reintroduction(tmp_path: Path) -> None:
    artifact = _artifact()
    path = tmp_path / "operational-alerts.json"
    snapshot = artifact["result"]["raw_cases"][0]["observed"]
    snapshot["job_id"] = "private-marker-job"
    _resign_snapshot(snapshot)
    _resign_outer(artifact)
    _write(path, artifact)

    with pytest.raises(ValueError, match="forbidden key"):
        verify_operational_alert_artifact(path)
