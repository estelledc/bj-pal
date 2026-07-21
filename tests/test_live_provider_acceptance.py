from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
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
from evals.live_model.scenarios import SCENARIOS  # noqa: E402
from evals.live_provider.acceptance import build_live_provider_acceptance  # noqa: E402
from evals.live_provider.credential_source import load_csswitch_credential  # noqa: E402
from evals.live_provider.verify import verify_live_provider_acceptance  # noqa: E402
from evals.run_live_provider_acceptance import _write_mode_0600  # noqa: E402


SECRET = "sk-test-live-provider-credential"


def _sha(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _rehash(payload: dict) -> None:
    canonical = deepcopy(payload)
    canonical.pop("artifact_sha256", None)
    payload["artifact_sha256"] = _sha(canonical)


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _config_payload(**profile_overrides) -> dict:
    profile = {
        "id": "active-profile",
        "template_id": "deepseek",
        "api_format": "anthropic",
        "base_url": "https://api.deepseek.example/anthropic",
        "api_key": SECRET,
    }
    profile.update(profile_overrides)
    return {
        "schema_version": 2,
        "active_id": "active-profile",
        "profiles": [profile],
    }


def _write_config(path: Path, payload: dict | None = None, *, mode: int = 0o600) -> None:
    path.write_text(
        json.dumps(payload or _config_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    path.chmod(mode)


def _live_artifacts(monkeypatch) -> tuple[dict, dict, dict]:
    monkeypatch.setenv("BJ_PAL_LLM", "mock")
    scenario = SCENARIOS["synthetic-friends-sanlitun-3h-budget"]
    result = PlanningService().execute(scenario.request())
    snapshot = result.final_plan.model_output_context
    assert isinstance(snapshot, dict)
    observation = build_live_model_observation(
        observed_at="2026-07-21T08:00:00Z",
        scenario_id=scenario.scenario_id,
        provider="dpsk",
        model="deepseek-v4-pro",
        endpoint_base_url="https://api.deepseek.example/anthropic",
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
        elapsed_ms=1200.0,
        model_output_contract=snapshot,
        recording_method="csswitch_handoff_runner",
    )
    quality = build_live_plan_quality_artifact(
        observed_at=observation["observed_at"],
        scenario=scenario,
        provider=observation["provider"],
        linked_observation_sha256=observation["artifact_sha256"],
        result=result,
    )
    execution = result.execution.to_dict()
    llm_spans = [
        span
        for span in execution["spans"]
        if span["name"].startswith("llm.") and span["name"].endswith(".complete")
    ]
    assert len(llm_spans) == 1
    llm_spans[0]["input_tokens"] = 120
    llm_spans[0]["output_tokens"] = 80
    execution["token_usage"] = {
        "completeness": "complete",
        "reported_calls": 1,
        "input_tokens": 120,
        "output_tokens": 80,
    }
    budget = execution["execution_budget"]
    budget["usage"]["reported_token_call_count"] = 1
    budget["usage"]["reported_total_tokens"] = 200
    _rehash(budget)
    _rehash(execution)
    return observation, quality, execution


def _receipt(monkeypatch) -> tuple[dict, dict, dict]:
    observation, quality, execution = _live_artifacts(monkeypatch)
    receipt = build_live_provider_acceptance(
        observation=observation,
        quality=quality,
        execution=execution,
        credential_metadata={
            "source_type": "csswitch_active_profile",
            "config_file_mode": "0600",
            "owner_uid_match": True,
            "regular_file": True,
            "symlink": False,
            "profile_template": "deepseek",
            "api_format": "anthropic",
        },
        credential_value=SECRET,
        explicit_cost_ack=True,
    )
    return receipt, observation, quality


def test_csswitch_loader_requires_owner_only_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_config(path)
    credential = load_csswitch_credential(path)
    assert credential.base_url == "https://api.deepseek.example/anthropic"
    assert credential.safe_metadata()["config_file_mode"] == "0600"
    assert SECRET not in repr(credential)


def test_csswitch_loader_rejects_group_readable_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_config(path, mode=0o640)
    with pytest.raises(ValueError, match="group/other"):
        load_csswitch_credential(path)


def test_csswitch_loader_rejects_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    link = tmp_path / "config.json"
    _write_config(source)
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        load_csswitch_credential(link)


def test_csswitch_loader_rejects_non_https_endpoint(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_config(path, _config_payload(base_url="http://example.test/anthropic"))
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        load_csswitch_credential(path)


def test_provider_environment_is_explicit_and_restored(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_config(path)
    credential = load_csswitch_credential(path)
    environ = {"LONGCAT_API_KEY": "old-value", "DPSK_MODEL": "old-model"}
    with credential.provider_environment(
        model="deepseek-v4-pro",
        max_output_tokens=4096,
        environ=environ,
    ):
        assert environ["BJ_PAL_LLM"] == "dpsk"
        assert environ["DPSK_API_KEY"] == SECRET
        assert environ["DPSK_MODEL"] == "deepseek-v4-pro"
        assert "LONGCAT_API_KEY" not in environ
    assert environ == {"LONGCAT_API_KEY": "old-value", "DPSK_MODEL": "old-model"}


def test_acceptance_verifier_recomputes_usage_quality_and_gate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    receipt, observation, quality = _receipt(monkeypatch)
    receipt_path = tmp_path / "acceptance.json"
    observation_path = tmp_path / "observation.json"
    quality_path = tmp_path / "quality.json"
    _write(receipt_path, receipt)
    _write(observation_path, observation)
    _write(quality_path, quality)

    verified = verify_live_provider_acceptance(
        receipt_path, observation_path, quality_path
    )
    assert verified["acceptance"]["gate_pass"] is True
    usage = verified["execution_evidence"]["provider_reported_usage"]
    assert usage == {
        "completeness": "complete",
        "reported_calls": 1,
        "input_tokens": 120,
        "output_tokens": 80,
        "total_tokens": 200,
    }
    rendered = json.dumps(verified, ensure_ascii=False)
    assert SECRET not in rendered
    assert "/Users/" not in rendered


def test_acceptance_verifier_rejects_rehashed_usage_tampering(
    monkeypatch,
    tmp_path: Path,
) -> None:
    receipt, observation, quality = _receipt(monkeypatch)
    receipt["execution_evidence"]["provider_reported_usage"]["total_tokens"] = 199
    _rehash(receipt)
    receipt_path = tmp_path / "acceptance.json"
    observation_path = tmp_path / "observation.json"
    quality_path = tmp_path / "quality.json"
    _write(receipt_path, receipt)
    _write(observation_path, observation)
    _write(quality_path, quality)
    with pytest.raises(ValueError, match="token total"):
        verify_live_provider_acceptance(receipt_path, observation_path, quality_path)


def test_acceptance_builder_rejects_exact_credential_in_linked_artifact(
    monkeypatch,
) -> None:
    observation, quality, execution = _live_artifacts(monkeypatch)
    quality["debug_value"] = SECRET
    _rehash(quality)
    with pytest.raises(ValueError, match="credential value appeared"):
        build_live_provider_acceptance(
            observation=observation,
            quality=quality,
            execution=execution,
            credential_metadata={
                "source_type": "csswitch_active_profile",
                "config_file_mode": "0600",
                "owner_uid_match": True,
                "regular_file": True,
                "symlink": False,
                "profile_template": "deepseek",
                "api_format": "anthropic",
            },
            credential_value=SECRET,
            explicit_cost_ack=True,
        )


def test_mode_0600_writer_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    _write_mode_0600(path, {"ok": True})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        _write_mode_0600(path, {"ok": False})


def test_live_acceptance_cli_requires_explicit_cost_ack(tmp_path: Path) -> None:
    output_dir = tmp_path / "must-not-exist"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "evals" / "run_live_provider_acceptance.py"),
            "--output-dir",
            str(output_dir),
            "--credential-source",
            "csswitch",
            "--model",
            "deepseek-v4-pro",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={},
    )
    assert result.returncode == 2
    assert "--ack-provider-cost is required" in result.stderr
    assert not output_dir.exists()
