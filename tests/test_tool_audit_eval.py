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

from evals.tool_audit.evaluate import evaluate_tool_audit  # noqa: E402
from evals.tool_audit.verify import verify_tool_audit_artifact  # noqa: E402


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


def test_tool_audit_artifact_recomputes_privacy_and_integrity_contract(
    tmp_path: Path,
) -> None:
    artifact = evaluate_tool_audit()
    path = tmp_path / "tool-audit.json"
    _write(path, artifact)

    verified = verify_tool_audit_artifact(path)

    assert verified["classification"] == "synthetic_contract"
    assert verified["result"]["metrics"] == {
        "case_count": 5,
        "privacy_projection_rate": 1.0,
        "chain_integrity_rate": 1.0,
        "append_only_enforcement_rate": 1.0,
        "reset_visibility_rate": 1.0,
        "legacy_payload_hiding_rate": 1.0,
        "storage_isolation_rate": 1.0,
    }


def test_tool_audit_verifier_rejects_rehashed_private_marker(tmp_path: Path) -> None:
    artifact = evaluate_tool_audit()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "privacy_projection"
    )
    event = case["events"][0]
    event["params"]["leak"] = "private-itinerary-marker"
    body = deepcopy(event)
    body.pop("event_sha256")
    event["event_sha256"] = _sha(body)
    _resign(tampered)
    path = tmp_path / "private-marker.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_tool_audit_artifact(path)


def test_tool_audit_verifier_rejects_fabricated_mutation_success(
    tmp_path: Path,
) -> None:
    artifact = evaluate_tool_audit()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "append_only_chain"
    )
    case["mutation_results"]["delete"] = "mutation_allowed"
    _resign(tampered)
    path = tmp_path / "mutation-allowed.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_tool_audit_artifact(path)


def test_tool_audit_verifier_rejects_reset_that_discards_chain_history(
    tmp_path: Path,
) -> None:
    artifact = evaluate_tool_audit()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "reset_visibility"
    )
    case["events"] = case["events"][1:]
    _resign(tampered)
    path = tmp_path / "truncated-reset-chain.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_tool_audit_artifact(path)


def test_tool_audit_verifier_rejects_fabricated_storage_isolation(
    tmp_path: Path,
) -> None:
    artifact = evaluate_tool_audit()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "storage_isolation"
    )
    case["legacy_sha256_after"] = "0" * 64
    _resign(tampered)
    path = tmp_path / "fabricated-storage-isolation.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_tool_audit_artifact(path)
