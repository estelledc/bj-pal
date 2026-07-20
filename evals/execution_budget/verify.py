"""Independently verify execution-budget enforcement claims."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def _sha(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def verify_execution_budget_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported execution-budget artifact schema")
    canonical = deepcopy(artifact)
    artifact_sha = canonical.pop("artifact_sha256", None)
    if artifact_sha != _sha(canonical):
        raise ValueError("execution-budget artifact SHA-256 mismatch")

    result = artifact.get("result") or {}
    cases = result.get("raw_cases") or []
    if not cases or len({case.get("case_id") for case in cases}) != len(cases):
        raise ValueError("execution-budget cases must be non-empty and unique")
    outcomes = [_verify_case(case) for case in cases]
    terminated = [
        outcome
        for case, outcome in zip(cases, outcomes)
        if case.get("expected_status") == "terminated"
    ]
    metrics = {
        "case_count": len(cases),
        "snapshot_integrity_rate": _rate(outcomes, "snapshot_integrity"),
        "termination_semantics_rate": _rate(outcomes, "termination_semantics"),
        "post_limit_work_blocked_rate": _rate(terminated, "post_limit_work_blocked"),
        "privacy_marker_exclusion_rate": _rate(outcomes, "privacy_marker_excluded"),
    }
    if result.get("metrics") != metrics:
        raise ValueError("execution-budget metrics do not match raw cases")
    if any(value != 1.0 for key, value in metrics.items() if key != "case_count"):
        raise ValueError("execution-budget contract gate did not pass")
    return artifact


def _verify_case(case: dict[str, Any]) -> dict[str, bool]:
    snapshot = case.get("snapshot") or {}
    canonical = deepcopy(snapshot)
    snapshot_sha = canonical.pop("artifact_sha256", None)
    integrity = (
        snapshot.get("version") == "execution_budget_v1"
        and snapshot_sha == _sha(canonical)
    )
    policy = snapshot.get("policy") or {}
    usage = snapshot.get("usage") or {}
    status = snapshot.get("status")
    reason = snapshot.get("termination_reason")
    expected_status = case.get("expected_status")
    expected_reason = case.get("expected_reason")

    semantics = status == expected_status and reason == expected_reason
    if semantics and reason == "completed":
        semantics = (
            usage.get("llm_call_count", -1) <= policy.get("max_llm_calls", -1)
            and usage.get("data_provider_batch_count", -1)
            <= policy.get("max_data_provider_batches", -1)
            and usage.get("tool_call_count", -1) <= policy.get("max_tool_calls", -1)
            and usage.get("reported_total_tokens", 0)
            <= policy.get("max_reported_tokens", -1)
            and float(usage.get("elapsed_ms", -1))
            <= policy.get("max_wall_clock_ms", -1)
        )
    elif semantics and reason == "llm_call_limit":
        semantics = usage.get("llm_call_count") == policy.get("max_llm_calls") + 1
    elif semantics and reason == "data_provider_batch_limit":
        semantics = (
            usage.get("data_provider_batch_count")
            == policy.get("max_data_provider_batches") + 1
        )
    elif semantics and reason == "tool_call_limit":
        semantics = usage.get("tool_call_count") == policy.get("max_tool_calls") + 1
    elif semantics and reason == "reported_token_limit":
        semantics = (
            usage.get("reported_total_tokens") is not None
            and usage.get("reported_total_tokens") > policy.get("max_reported_tokens")
        )
    elif semantics and reason == "wall_clock_limit":
        semantics = float(usage.get("elapsed_ms", -1)) > policy.get(
            "max_wall_clock_ms", -1
        )

    marker = case.get("forbidden_marker")
    privacy = marker is None or marker not in json.dumps(snapshot, ensure_ascii=False)
    return {
        "snapshot_integrity": integrity,
        "termination_semantics": semantics,
        "post_limit_work_blocked": not bool(case.get("post_limit_work_executed")),
        "privacy_marker_excluded": privacy,
    }


def _rate(outcomes: list[dict[str, bool]], key: str) -> float:
    if not outcomes:
        raise ValueError("execution-budget metric has no applicable cases")
    return round(sum(item[key] for item in outcomes) / len(outcomes), 3)
