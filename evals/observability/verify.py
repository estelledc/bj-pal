"""Independently recompute execution-observation artifact claims."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


REQUIRED_PHASES = {
    "planning.preflight",
    "planning.generate",
    "planning.probe_and_replan",
    "planning.persist_trace",
    "planning.load_data_profile",
}


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def verify_observability_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported observability artifact schema")
    canonical = deepcopy(artifact)
    observed_artifact_sha = canonical.pop("artifact_sha256", None)
    if observed_artifact_sha != _canonical_sha256(canonical):
        raise ValueError("observability artifact SHA-256 mismatch")

    result = artifact.get("result") or {}
    raw_cases = result.get("raw_cases") or []
    if not raw_cases or len({item.get("case_id") for item in raw_cases}) != len(raw_cases):
        raise ValueError("observability cases must be non-empty and uniquely identified")
    outcomes = [_verify_case(case) for case in raw_cases]
    metrics = {
        "case_count": len(outcomes),
        "integrity_rate": _rate(outcomes, "integrity_valid"),
        "span_tree_valid_rate": _rate(outcomes, "span_tree_valid"),
        "operation_count_valid_rate": _rate(outcomes, "operation_count_valid"),
        "token_semantics_valid_rate": _rate(outcomes, "token_semantics_valid"),
        "privacy_marker_exclusion_rate": _rate(outcomes, "privacy_marker_excluded"),
    }
    if result.get("metrics") != metrics:
        raise ValueError("observability metrics do not match raw cases")
    if any(value != 1.0 for key, value in metrics.items() if key != "case_count"):
        raise ValueError("observability contract gate did not pass")
    return artifact


def _verify_case(case: dict[str, Any]) -> dict[str, bool]:
    observation = case.get("observation") or {}
    payload = deepcopy(observation)
    observation_sha = payload.pop("artifact_sha256", None)
    integrity_valid = observation_sha == _canonical_sha256(payload)

    spans = observation.get("spans") or []
    by_id = {item.get("span_id"): item for item in spans}
    roots = [item for item in spans if item.get("parent_span_id") is None]
    tree_valid = (
        len(by_id) == len(spans)
        and None not in by_id
        and len(roots) == 1
        and roots[0].get("name") == "planning.execute"
        and REQUIRED_PHASES.issubset({item.get("name") for item in spans})
    )
    for span in spans:
        parent_id = span.get("parent_span_id")
        if parent_id is not None and parent_id not in by_id:
            tree_valid = False
        if span.get("status") not in {"ok", "error"}:
            tree_valid = False
        if float(span.get("offset_ms", -1)) < 0 or float(span.get("duration_ms", -1)) < 0:
            tree_valid = False

    llm_spans = [
        item
        for item in spans
        if str(item.get("name") or "").startswith("llm.")
        and str(item.get("name") or "").endswith(".complete")
    ]
    tool_spans = [
        item for item in spans if str(item.get("name") or "").startswith("tool.")
    ]
    provider_batches = [item for item in spans if item.get("name") == "planner.collect_data"]
    expected_operations = {
        "span_count": len(spans),
        "llm_call_count": len(llm_spans),
        "data_provider_batch_count": len(provider_batches),
        "tool_call_count": len(tool_spans),
    }
    operation_valid = (
        observation.get("operation_counts") == expected_operations
        and len(llm_spans) == case.get("expected_llm_call_count")
    )

    reported = [
        item
        for item in llm_spans
        if item.get("input_tokens") is not None or item.get("output_tokens") is not None
    ]
    if not llm_spans:
        completeness = "not_applicable"
    elif not reported:
        completeness = "unavailable"
    elif len(reported) == len(llm_spans):
        completeness = "complete"
    else:
        completeness = "partial"
    token_usage = observation.get("token_usage") or {}
    token_valid = token_usage == {
        "completeness": completeness,
        "reported_calls": len(reported),
        "input_tokens": (
            sum(int(item.get("input_tokens") or 0) for item in reported)
            if reported
            else None
        ),
        "output_tokens": (
            sum(int(item.get("output_tokens") or 0) for item in reported)
            if reported
            else None
        ),
    } and completeness == case.get("expected_usage_completeness")

    marker = case.get("forbidden_marker")
    serialized_observation = json.dumps(observation, ensure_ascii=False)
    privacy_valid = marker is None or marker not in serialized_observation
    if observation.get("correlation_id") != case.get("expected_correlation_id"):
        tree_valid = False
    return {
        "integrity_valid": integrity_valid,
        "span_tree_valid": tree_valid,
        "operation_count_valid": operation_valid,
        "token_semantics_valid": token_valid,
        "privacy_marker_excluded": privacy_valid,
    }


def _rate(outcomes: list[dict[str, bool]], key: str) -> float:
    return round(sum(item[key] for item in outcomes) / len(outcomes), 3)
