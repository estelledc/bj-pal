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

from evals.state_layout.evaluate import evaluate_state_layout  # noqa: E402
from evals.state_layout.verify import verify_state_layout_artifact  # noqa: E402


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
    body = deepcopy(artifact)
    body.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = _sha(body)


def test_state_layout_artifact_recomputes_all_contract_metrics(tmp_path: Path) -> None:
    artifact = evaluate_state_layout()
    path = tmp_path / "state-layout.json"
    _write(path, artifact)

    verified = verify_state_layout_artifact(path)

    assert verified["result"]["metrics"] == {
        "case_count": 3,
        "dry_run_read_only_rate": 1.0,
        "source_preservation_rate": 1.0,
        "copy_integrity_rate": 1.0,
        "receipt_integrity_rate": 1.0,
        "domain_isolation_rate": 1.0,
        "legacy_classification_rate": 1.0,
    }


def test_state_layout_verifier_rejects_fabricated_source_preservation(
    tmp_path: Path,
) -> None:
    artifact = evaluate_state_layout()
    case = next(
        item
        for item in artifact["result"]["raw_cases"]
        if item["case_id"] == "verified_copy"
    )
    case["source_file_sha256_after"] = "0" * 64
    _resign(artifact)
    path = tmp_path / "source-mutated.json"
    _write(path, artifact)

    with pytest.raises(ValueError, match="metrics"):
        verify_state_layout_artifact(path)


def test_state_layout_verifier_rejects_resigned_receipt_tampering(
    tmp_path: Path,
) -> None:
    artifact = evaluate_state_layout()
    case = next(
        item
        for item in artifact["result"]["raw_cases"]
        if item["case_id"] == "verified_copy"
    )
    case["metadata"]["body"]["destination_counts"]["plan_trace"] += 1
    _resign(artifact)
    path = tmp_path / "receipt-tampered.json"
    _write(path, artifact)

    with pytest.raises(ValueError, match="metrics"):
        verify_state_layout_artifact(path)
