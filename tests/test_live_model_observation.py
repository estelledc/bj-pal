from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.live_model.observation import build_live_model_observation  # noqa: E402
from evals.live_model.scenarios import SCENARIOS  # noqa: E402
from evals.live_model.verify import (  # noqa: E402
    verify_live_model_observation,
    verify_live_model_pair,
    verify_live_model_suite,
)
from application import PlanningService  # noqa: E402


def _sha(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _snapshot() -> dict:
    payload = {
        "version": "model_output_contract_v1",
        "status": "rejected",
        "attempt_count": 2,
        "repair_attempted": True,
        "candidate_count": 26,
        "issue_codes": ["depart_duration_invalid", "schema_literal_invalid"],
    }
    payload["artifact_sha256"] = _sha(payload)
    return payload


def _artifact() -> dict:
    return build_live_model_observation(
        observed_at="2026-07-20T10:20:00+08:00",
        scenario_id="synthetic-friends-sanlitun-3h-budget",
        provider="dpsk",
        model="test-model",
        endpoint_base_url="https://example.invalid/anthropic",
        execution_limits={
            "max_output_tokens": 4096,
            "max_llm_calls": 2,
            "max_data_provider_batches": 1,
            "max_tool_calls": 8,
            "max_transport_attempts_per_llm_call": 2,
            "max_reported_tokens": 16384,
            "max_wall_clock_ms": 90000,
        },
        outcome="rejected",
        elapsed_ms=63885.045,
        model_output_contract=_snapshot(),
    )


def _write(path: Path, artifact: dict) -> None:
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")


def _rehash(artifact: dict) -> None:
    canonical = deepcopy(artifact)
    canonical.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = _sha(canonical)


def test_live_model_observation_verifies_without_raw_content(tmp_path: Path) -> None:
    path = tmp_path / "observation.json"
    _write(path, _artifact())
    verified = verify_live_model_observation(path)
    assert verified["result"]["outcome"] == "rejected"
    assert verified["result"]["fail_closed"] is True
    rendered = json.dumps(verified, ensure_ascii=False).lower()
    assert "user_input" not in rendered
    assert "raw_output" not in rendered
    assert "api_key" not in rendered


def test_fixed_live_scenarios_preflight_without_clarification_or_conflict() -> None:
    service = PlanningService()
    assert len(SCENARIOS) == 3
    for scenario in SCENARIOS.values():
        result = service.preflight(scenario.request())
        assert result.requirements.status == "proceed"
        assert not result.constraints.conflicts
        assert result.request.persona == scenario.persona
        assert result.request.area_anchor == scenario.area_anchor


def test_live_model_verifier_rejects_self_rehashed_false_outcome(tmp_path: Path) -> None:
    artifact = _artifact()
    artifact["result"]["outcome"] = "accepted"
    artifact["result"]["error_code"] = None
    artifact["result"]["fail_closed"] = False
    _rehash(artifact)
    path = tmp_path / "tampered.json"
    _write(path, artifact)
    with pytest.raises(ValueError, match="snapshot status mismatch"):
        verify_live_model_observation(path)


def test_live_model_verifier_rejects_forbidden_field_even_when_rehashed(tmp_path: Path) -> None:
    artifact = _artifact()
    artifact["debug"] = {"prompt": "should never persist"}
    _rehash(artifact)
    path = tmp_path / "private.json"
    _write(path, artifact)
    with pytest.raises(ValueError, match="forbidden persisted field"):
        verify_live_model_observation(path)


def test_live_runner_requires_explicit_cost_ack(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "evals" / "run_live_model.py"),
            "--output",
            str(tmp_path / "must-not-exist.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={},
    )
    assert result.returncode == 2
    assert "--ack-provider-cost is required" in result.stderr
    assert not (tmp_path / "must-not-exist.json").exists()


def test_live_model_pair_preserves_single_sample_boundary() -> None:
    observations = ROOT / "evals" / "live_model" / "observations"
    result = verify_live_model_pair(
        observations / "2026-07-20-dpsk-flash-after-prompt-fix.json",
        observations / "2026-07-20-dpsk-pro-after-prompt-fix.json",
    )
    assert result["classification"] == "two_single_sample_model_selection_signal"
    assert result["decision"] == "prefer_pro_for_next_bounded_live_trials"
    assert [item["outcome"] for item in result["samples"]] == [
        "rejected",
        "accepted",
    ]
    assert "success_rate" not in json.dumps(result)


def test_live_model_pair_rejects_self_rehashed_limit_mismatch(tmp_path: Path) -> None:
    observations = ROOT / "evals" / "live_model" / "observations"
    flash_path = observations / "2026-07-20-dpsk-flash-after-prompt-fix.json"
    pro = json.loads(
        (observations / "2026-07-20-dpsk-pro-after-prompt-fix.json").read_text(
            encoding="utf-8"
        )
    )
    pro["execution_limits"]["max_output_tokens"] = 2048
    _rehash(pro)
    pro_path = tmp_path / "pro-limit-mismatch.json"
    _write(pro_path, pro)

    with pytest.raises(ValueError, match="execution limits differ"):
        verify_live_model_pair(flash_path, pro_path)


def test_live_model_pro_suite_reports_counts_without_rate_claims() -> None:
    observations = ROOT / "evals" / "live_model" / "observations"
    result = verify_live_model_suite(
        [
            observations / "2026-07-20-dpsk-pro-after-prompt-fix.json",
            observations / "2026-07-20-dpsk-pro-family-wudaoying.json",
            observations / "2026-07-20-dpsk-pro-solo-798.json",
        ]
    )
    assert result["case_count"] == 3
    assert result["accepted_count"] == 3
    assert result["first_pass_count"] == 3
    assert result["gate_pass"] is True
    assert result["candidate_count_range"] == {"min": 2, "max": 27}
    assert "success_rate" not in json.dumps(result)


def test_live_model_suite_rejects_duplicate_scenario() -> None:
    observations = ROOT / "evals" / "live_model" / "observations"
    sanlitun = observations / "2026-07-20-dpsk-pro-after-prompt-fix.json"
    family = observations / "2026-07-20-dpsk-pro-family-wudaoying.json"
    with pytest.raises(ValueError, match="scenario set is incomplete or duplicated"):
        verify_live_model_suite([sanlitun, family, family])
