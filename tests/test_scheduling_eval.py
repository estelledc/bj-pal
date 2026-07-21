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

from evals.scheduling.evaluate import evaluate_scheduling  # noqa: E402
from evals.scheduling.verify import verify_scheduling_artifact  # noqa: E402


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


def test_scheduling_artifact_recomputes_ordering_and_wait_evidence(tmp_path: Path) -> None:
    artifact = evaluate_scheduling()
    path = tmp_path / "scheduling.json"
    _write(path, artifact)

    verified = verify_scheduling_artifact(path)

    assert verified["classification"] == "synthetic_contract"
    assert verified["result"]["metrics"] == {
        "case_count": 4,
        "ordering_accuracy_rate": 1.0,
        "effective_priority_evidence_rate": 1.0,
        "queue_wait_evidence_rate": 1.0,
        "starvation_case_pass_rate": 1.0,
        "backoff_exclusion_pass_rate": 1.0,
        "tenant_fairness_pass_rate": 1.0,
    }


def test_scheduling_verifier_rejects_candidate_evidence_that_changes_selection(
    tmp_path: Path,
) -> None:
    artifact = evaluate_scheduling()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "priority_preemption"
    )
    selected = next(
        candidate
        for candidate in case["candidates"]
        if candidate["job_id"] == case["expected_job_id"]
    )
    selected["base_priority"] = 0
    _resign(tampered)
    path = tmp_path / "tampered-selection.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="selection"):
        verify_scheduling_artifact(path)


def test_scheduling_verifier_rejects_fabricated_queue_wait(tmp_path: Path) -> None:
    artifact = evaluate_scheduling()
    tampered = deepcopy(artifact)
    case = tampered["result"]["raw_cases"][0]
    case["claim_event"]["payload"]["queue_wait_ms"] += 60_000
    _resign(tampered)
    path = tmp_path / "tampered-wait.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="metrics"):
        verify_scheduling_artifact(path)


def test_scheduling_verifier_rejects_fabricated_tenant_service_cursor(
    tmp_path: Path,
) -> None:
    artifact = evaluate_scheduling()
    tampered = deepcopy(artifact)
    case = next(
        item
        for item in tampered["result"]["raw_cases"]
        if item["case_id"] == "tenant_fairness"
    )
    selected = next(
        candidate
        for candidate in case["candidates"]
        if candidate["job_id"] == case["expected_job_id"]
    )
    selected["tenant_last_claimed_event_id_before"] = 999
    _resign(tampered)
    path = tmp_path / "tampered-tenant-cursor.json"
    _write(path, tampered)

    with pytest.raises(ValueError, match="selection"):
        verify_scheduling_artifact(path)
