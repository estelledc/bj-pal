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

from evals.side_effects.evaluate import evaluate_side_effects  # noqa: E402
from evals.side_effects.verify import verify_side_effect_artifact  # noqa: E402


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


def test_side_effect_artifact_recomputes_all_safety_metrics(tmp_path: Path) -> None:
    artifact = evaluate_side_effects()
    path = tmp_path / "side-effects.json"
    _write(path, artifact)

    verified = verify_side_effect_artifact(path)

    assert verified["result"]["metrics"] == {
        "case_count": 5,
        "separation_of_duty_rate": 1.0,
        "approval_binding_rate": 1.0,
        "idempotency_rate": 1.0,
        "tenant_isolation_rate": 1.0,
        "expiry_fail_closed_rate": 1.0,
        "receipt_integrity_rate": 1.0,
        "append_only_audit_rate": 1.0,
        "sandbox_enforcement_rate": 1.0,
        "uncertainty_no_retry_rate": 1.0,
        "status_lookup_resolution_rate": 1.0,
        "status_lookup_binding_rate": 1.0,
        "reconciliation_audit_rate": 1.0,
    }


def test_side_effect_verifier_rejects_fabricated_receipt(tmp_path: Path) -> None:
    artifact = evaluate_side_effects()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "approval_and_receipt"
    )
    case["receipt"]["request_sha256"] = "0" * 64
    case["receipt_sha256"] = _sha(case["receipt"])
    _resign(tampered)
    path = tmp_path / "tampered-receipt.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_side_effect_artifact(path)


def test_side_effect_verifier_rejects_claimed_automatic_retry(tmp_path: Path) -> None:
    artifact = evaluate_side_effects()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "uncertainty_no_retry"
    )
    case["automatic_retry_claimed"] = True
    _resign(tampered)
    path = tmp_path / "tampered-retry.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_side_effect_artifact(path)


def test_side_effect_verifier_rejects_tampered_reconciliation_evidence(
    tmp_path: Path,
) -> None:
    artifact = evaluate_side_effects()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "status_reconciliation"
    )
    case["reconciliation"]["evidence"]["provider_payload"][
        "quote_reference"
    ] = "tampered-quote"
    _resign(tampered)
    path = tmp_path / "tampered-reconciliation.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_side_effect_artifact(path)
