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

from application import PlanningService  # noqa: E402
from evals.live_model.observation import build_live_model_observation  # noqa: E402
from evals.live_model.quality import build_live_plan_quality_artifact  # noqa: E402
from evals.live_model.quality_verify import (  # noqa: E402
    verify_live_plan_quality,
    verify_live_plan_quality_suite,
)
from evals.live_model.scenarios import SCENARIOS  # noqa: E402


def _sha(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _rehash(artifact: dict) -> None:
    canonical = deepcopy(artifact)
    canonical.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = _sha(canonical)


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _artifacts(monkeypatch) -> tuple[dict, dict]:
    monkeypatch.setenv("BJ_PAL_LLM", "mock")
    scenario = SCENARIOS["synthetic-family-wudaoying-4h-child-diet"]
    result = PlanningService().execute(scenario.request())
    snapshot = result.final_plan.model_output_context
    assert isinstance(snapshot, dict)
    observation = build_live_model_observation(
        observed_at="2026-07-20T12:00:00Z",
        scenario_id=scenario.scenario_id,
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
        outcome=str(snapshot["status"]),
        elapsed_ms=100.0,
        model_output_contract=snapshot,
    )
    quality = build_live_plan_quality_artifact(
        observed_at=observation["observed_at"],
        scenario=scenario,
        provider=observation["provider"],
        linked_observation_sha256=observation["artifact_sha256"],
        result=result,
    )
    return observation, quality


def test_live_plan_quality_recomputes_hard_constraints_without_free_text(
    monkeypatch,
    tmp_path: Path,
) -> None:
    observation, quality = _artifacts(monkeypatch)
    observation_path = tmp_path / "observation.json"
    quality_path = tmp_path / "quality.json"
    _write(observation_path, observation)
    _write(quality_path, quality)

    verified = verify_live_plan_quality(quality_path, observation_path)
    assert verified["metrics"]["hard_gate_pass"] is True
    assert verified["metrics"]["not_evaluable_count"] == 0
    rendered = json.dumps(verified, ensure_ascii=False).lower()
    assert '"rationale"' not in rendered
    assert '"user_input"' not in rendered
    assert '"raw_output"' not in rendered


def test_live_plan_quality_rejects_rehashed_diet_evidence_tampering(
    monkeypatch,
    tmp_path: Path,
) -> None:
    observation, quality = _artifacts(monkeypatch)
    for fact in quality["selected_poi_facts"]:
        fact["positive_evidence_tags"] = [
            tag for tag in fact["positive_evidence_tags"] if tag != "no_spicy"
        ]
    _rehash(quality)
    observation_path = tmp_path / "observation.json"
    quality_path = tmp_path / "quality.json"
    _write(observation_path, observation)
    _write(quality_path, quality)

    with pytest.raises(ValueError, match="checks do not match raw projection"):
        verify_live_plan_quality(quality_path, observation_path)


def test_live_plan_quality_rejects_rehashed_free_text_field(
    monkeypatch,
    tmp_path: Path,
) -> None:
    observation, quality = _artifacts(monkeypatch)
    quality["debug"] = {"rationale": "must not persist"}
    _rehash(quality)
    observation_path = tmp_path / "observation.json"
    quality_path = tmp_path / "quality.json"
    _write(observation_path, observation)
    _write(quality_path, quality)

    with pytest.raises(ValueError, match="forbidden persisted field"):
        verify_live_plan_quality(quality_path, observation_path)


def test_live_plan_quality_rejects_rehashed_policy_relaxation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    observation, quality = _artifacts(monkeypatch)
    quality["policy"]["required_diet_evidence_tags"] = []
    _rehash(quality)
    observation_path = tmp_path / "observation.json"
    quality_path = tmp_path / "quality.json"
    _write(observation_path, observation)
    _write(quality_path, quality)

    with pytest.raises(ValueError, match="policy differs from fixed registry"):
        verify_live_plan_quality(quality_path, observation_path)


def test_checked_in_live_plan_quality_suite_reports_counts_not_rates() -> None:
    quality_root = ROOT / "evals" / "live_model" / "quality_artifacts"
    observation_root = ROOT / "evals" / "live_model" / "observations"
    result = verify_live_plan_quality_suite(
        [
            (
                quality_root / "2026-07-20-dpsk-pro-sanlitun-quality-v2.json",
                observation_root / "2026-07-20-dpsk-pro-sanlitun-quality-v2.json",
            ),
            (
                quality_root / "2026-07-20-dpsk-pro-family-quality-v2.json",
                observation_root / "2026-07-20-dpsk-pro-family-quality-v2.json",
            ),
            (
                quality_root / "2026-07-20-dpsk-pro-solo-quality-v2.json",
                observation_root / "2026-07-20-dpsk-pro-solo-quality-v2.json",
            ),
        ]
    )

    assert result["case_count"] == 3
    assert result["hard_gate_pass_count"] == 3
    assert result["not_evaluable_count"] == 0
    assert result["suite_gate_pass"] is True
    assert "success_rate" not in json.dumps(result)
