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

from evals.access_control.evaluate import evaluate_access_control  # noqa: E402
from evals.access_control.verify import verify_access_control_artifact  # noqa: E402


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


def _resign(artifact: dict) -> None:
    payload = deepcopy(artifact)
    payload.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = _sha(payload)


def test_access_control_artifact_recomputes_all_policy_boundaries(tmp_path: Path) -> None:
    artifact = evaluate_access_control()
    path = tmp_path / "access-control.json"
    _write(path, artifact)

    verified = verify_access_control_artifact(path)

    assert verified["classification"] == "synthetic_contract"
    assert verified["result"]["metrics"] == {
        "case_count": 6,
        "route_scope_enforcement_rate": 1.0,
        "priority_cap_enforcement_rate": 1.0,
        "tenant_isolation_rate": 1.0,
        "idempotency_namespace_rate": 1.0,
        "continuation_isolation_rate": 1.0,
        "credential_exclusion_rate": 1.0,
        "active_job_limit_enforcement_rate": 1.0,
        "submission_rate_enforcement_rate": 1.0,
        "admission_audit_rate": 1.0,
        "continuation_admission_recovery_rate": 1.0,
    }


def test_access_control_verifier_rejects_fabricated_scope_success(tmp_path: Path) -> None:
    artifact = evaluate_access_control()
    tampered = deepcopy(artifact)
    scope_case = next(
        case
        for case in tampered["result"]["raw_cases"]
        if case["case_id"] == "route_scope_matrix"
    )
    denied = next(
        request
        for request in scope_case["requests"]
        if request["actor"] == "alpha-reader" and request["operation"] == "jobs:submit"
    )
    denied["observed_status"] = 202
    denied["error_code"] = None
    _resign(tampered)
    path = tmp_path / "tampered-scope.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_access_control_artifact(path)


def test_access_control_verifier_rejects_fabricated_allowed_route_failure(
    tmp_path: Path,
) -> None:
    artifact = evaluate_access_control()
    tampered = deepcopy(artifact)
    scope_case = next(
        case
        for case in tampered["result"]["raw_cases"]
        if case["case_id"] == "route_scope_matrix"
    )
    allowed = next(
        request
        for request in scope_case["requests"]
        if request["actor"] == "alpha-reader" and request["operation"] == "jobs:read"
    )
    allowed["observed_status"] = 500
    allowed["error_code"] = "fabricated_failure"
    _resign(tampered)
    path = tmp_path / "tampered-allowed-route.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_access_control_artifact(path)


def test_access_control_verifier_rejects_embedded_credential_value(tmp_path: Path) -> None:
    artifact = evaluate_access_control()
    tampered = deepcopy(artifact)
    leaked_value = "eval-alpha-reader-0123456789-abcdef-000002"
    tampered["result"]["raw_cases"][0]["leaked_value"] = leaked_value
    _resign(tampered)
    path = tmp_path / "tampered-credential.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_access_control_artifact(path)


def test_access_control_verifier_rejects_fabricated_admission_audit(
    tmp_path: Path,
) -> None:
    artifact = evaluate_access_control()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "tenant_admission"
    )
    rejected = next(
        event for event in case["audit_events"] if event["decision"] == "rejected"
    )
    rejected["job_id"] = case["audit_events"][0]["job_id"]
    _resign(tampered)
    path = tmp_path / "tampered-admission-audit.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_access_control_artifact(path)
