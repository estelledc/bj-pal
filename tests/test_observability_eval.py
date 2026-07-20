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

from evals.observability.evaluate import evaluate_observability  # noqa: E402
from evals.observability.verify import verify_observability_artifact  # noqa: E402


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


def test_observability_artifact_recomputes_all_contract_metrics(tmp_path: Path) -> None:
    artifact = evaluate_observability()
    path = tmp_path / "observability.json"
    _write(path, artifact)

    verified = verify_observability_artifact(path)
    assert verified["classification"] == "synthetic_contract"
    assert verified["result"]["metrics"] == {
        "case_count": 3,
        "integrity_rate": 1.0,
        "span_tree_valid_rate": 1.0,
        "operation_count_valid_rate": 1.0,
        "token_semantics_valid_rate": 1.0,
        "privacy_marker_exclusion_rate": 1.0,
    }


def test_observability_verifier_rejects_fabricated_mock_token_usage(
    tmp_path: Path,
) -> None:
    artifact = evaluate_observability()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "mock_usage_unavailable"
    )
    llm_span = next(
        item
        for item in case["observation"]["spans"]
        if item["name"] == "llm.mock.complete"
    )
    llm_span["input_tokens"] = 999
    llm_span["output_tokens"] = 999
    case["observation"]["token_usage"] = {
        "completeness": "complete",
        "reported_calls": 1,
        "input_tokens": 999,
        "output_tokens": 999,
    }
    observation_payload = deepcopy(case["observation"])
    observation_payload.pop("artifact_sha256")
    case["observation"]["artifact_sha256"] = _sha(observation_payload)
    artifact_payload = deepcopy(tampered)
    artifact_payload.pop("artifact_sha256")
    tampered["artifact_sha256"] = _sha(artifact_payload)
    path = tmp_path / "tampered.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_observability_artifact(path)
