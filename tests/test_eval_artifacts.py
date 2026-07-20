from __future__ import annotations

import copy

import pytest

from evals.artifacts import (
    ArtifactVerificationError,
    build_artifact,
    seal_artifact,
    verify_artifact,
)
from data_profile import DataProfile


PROFILE = DataProfile(
    name="test-demo",
    classification="synthetic",
    public_reproducible=True,
    sources={"pois": "fixture"},
    counts={"pois": 1},
    limitations=("test only",),
)


def _build(reports: dict | None = None) -> dict:
    return build_artifact(reports or _reports(), command="test", profile=PROFILE)


def _reports() -> dict:
    l1 = {
        "level": "L1", "git_sha": "abc", "started_at": 1.0, "duration_s": 0.1,
        "n_cases": 1, "n_pass": 1, "pass_rate": 1.0,
        "results": [{"name": "anchor", "pass": True, "latency_ms": 1,
                     "observed": {"plan_id": "plan-a"}}],
    }
    module = {
        "module": "integration", "n_cases": 1, "n_pass": 1, "pass_rate": 1.0,
        "results": [{"name": "flow", "pass": True, "latency_ms": 2}],
    }
    l2 = {
        "level": "L2", "git_sha": "abc", "started_at": 1.0, "duration_s": 0.2,
        "n_cases": 1, "n_pass": 1, "pass_rate": 1.0, "modules": [module],
    }
    cases = [{
        "case_id": "c1", "persona": "family", "scenario": "sunny",
        "query": "带娃散步", "all_pass": True,
        "signals": {"S1": {"pass": True, "latency_ms": 3,
                            "observed": {"plan_id": "plan-a"}}},
    }]
    l3 = {
        "level": "L3", "git_sha": "abc", "started_at": 1.0, "duration_s": 0.3,
        "n_cases": 1, "n_all_pass": 1, "all_pass_rate": 1.0,
        "signal_summary": [{"signal": "S1", "pass": 1, "total": 1, "rate": 1.0}],
        "segment_summary": [{"persona": "family", "scenario": "sunny",
                             "pass": 1, "total": 1, "rate": 1.0}],
        "failed_cases": [], "cases": cases,
    }
    return {"L1": l1, "L2": l2, "L3": l3}


def test_build_and_verify_artifact() -> None:
    artifact = _build()
    summary = verify_artifact(artifact)
    assert summary["overall_pass"] is True
    assert summary["levels"]["L3"]["minimum_signal_rate"] == 1.0


def test_payload_tampering_is_rejected() -> None:
    artifact = _build()
    artifact["evaluations"]["L1"]["results"][0]["pass"] = False
    with pytest.raises(ArtifactVerificationError, match="payload_sha256 mismatch"):
        verify_artifact(artifact)


def test_semantic_digest_ignores_timing_and_generated_ids() -> None:
    first = _build()
    reports = _reports()
    reports["L1"]["duration_s"] = 99.0
    reports["L1"]["results"][0]["latency_ms"] = 999
    reports["L1"]["results"][0]["observed"]["plan_id"] = "plan-random"
    second = _build(reports)
    assert first["integrity"]["semantic_sha256"] == second["integrity"]["semantic_sha256"]
    assert first["integrity"]["payload_sha256"] != second["integrity"]["payload_sha256"]


def test_resealed_stale_summary_is_rejected() -> None:
    artifact = _build()
    stale = copy.deepcopy(artifact)
    stale["evaluations"]["L2"]["modules"][0]["n_pass"] = 0
    seal_artifact(stale)
    with pytest.raises(ArtifactVerificationError, match="claimed 0, recomputed 1"):
        verify_artifact(stale)


def test_resealed_online_backend_cannot_masquerade_as_public_artifact() -> None:
    artifact = _build()
    artifact["run"]["backend"] = "longcat"
    seal_artifact(artifact)
    with pytest.raises(ArtifactVerificationError, match="deterministic mock backend"):
        verify_artifact(artifact)
