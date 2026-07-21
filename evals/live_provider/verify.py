"""Independent verifier for bounded live-provider acceptance receipts.

This verifier does not import the acceptance builder or credential loader. It
can recompute integrity and gate semantics, but it cannot prove that a remote
provider executed the request or signed the returned metadata.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from evals.live_model.quality_verify import verify_live_plan_quality
from evals.live_model.verify import verify_live_model_observation


_EXPECTED_CHECKS = {
    "credential_preflight_pass",
    "live_result_accepted",
    "quality_hard_gate_pass",
    "provider_reported_usage_complete",
    "execution_budget_completed",
    "exact_credential_absent_from_linked_artifacts",
}
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "password",
    "prompt",
    "raw_model_output",
    "raw_output",
    "request_body",
    "secret",
    "user_input",
    "profile_id",
    "config_path",
}


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _reject_forbidden_keys(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise ValueError(f"forbidden persisted field at {path}.{key}")
            _reject_forbidden_keys(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_keys(item, path=f"{path}[{index}]")


def _verify_budget(budget: dict[str, Any]) -> None:
    canonical = deepcopy(budget)
    observed = canonical.pop("artifact_sha256", None)
    if budget.get("version") != "execution_budget_v1" or observed != _sha(canonical):
        raise ValueError("execution budget integrity mismatch")
    policy = budget.get("policy")
    usage = budget.get("usage")
    if not isinstance(policy, dict) or not isinstance(usage, dict):
        raise ValueError("execution budget policy/usage is missing")
    expected_policy = {
        "max_llm_calls",
        "max_data_provider_batches",
        "max_tool_calls",
        "max_transport_attempts_per_llm_call",
        "max_reported_tokens",
        "max_wall_clock_ms",
    }
    expected_usage = {
        "llm_call_count",
        "data_provider_batch_count",
        "tool_call_count",
        "reported_token_call_count",
        "reported_total_tokens",
        "elapsed_ms",
    }
    if set(policy) != expected_policy or set(usage) != expected_usage:
        raise ValueError("execution budget fields are incomplete")
    if any(not _is_int(value) or value < 0 for value in policy.values()):
        raise ValueError("execution budget policy values must be non-negative integers")
    for field in expected_usage - {"reported_total_tokens", "elapsed_ms"}:
        if not _is_int(usage[field]) or usage[field] < 0:
            raise ValueError("execution budget usage values are invalid")
    reported_total = usage["reported_total_tokens"]
    if reported_total is not None and (
        not _is_int(reported_total) or reported_total < 0
    ):
        raise ValueError("reported_total_tokens is invalid")
    elapsed = usage["elapsed_ms"]
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
        raise ValueError("execution budget elapsed_ms is invalid")


def _verify_execution(evidence: dict[str, Any]) -> dict[str, bool]:
    if set(evidence) != {
        "source_artifact_sha256",
        "version",
        "status",
        "duration_ms",
        "operation_counts",
        "provider_reported_usage",
        "execution_budget",
    }:
        raise ValueError("execution evidence fields are incomplete")
    if (
        evidence.get("version") != "execution_observation_v2"
        or evidence.get("status") != "succeeded"
        or not isinstance(evidence.get("source_artifact_sha256"), str)
        or len(evidence["source_artifact_sha256"]) != 64
    ):
        raise ValueError("execution evidence identity/status is invalid")
    duration = evidence.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
        raise ValueError("execution duration is invalid")
    operations = evidence.get("operation_counts")
    if not isinstance(operations, dict) or set(operations) != {
        "llm_call_count",
        "data_provider_batch_count",
        "tool_call_count",
    }:
        raise ValueError("operation counts are incomplete")
    if any(not _is_int(value) or value < 0 for value in operations.values()):
        raise ValueError("operation counts are invalid")
    usage = evidence.get("provider_reported_usage")
    if not isinstance(usage, dict) or set(usage) != {
        "completeness",
        "reported_calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }:
        raise ValueError("provider-reported usage fields are incomplete")
    for field in ("reported_calls", "input_tokens", "output_tokens", "total_tokens"):
        value = usage[field]
        if value is not None and (not _is_int(value) or value < 0):
            raise ValueError("provider-reported usage values are invalid")
    expected_total = (
        (usage["input_tokens"] or 0) + (usage["output_tokens"] or 0)
        if usage["input_tokens"] is not None or usage["output_tokens"] is not None
        else None
    )
    if usage["total_tokens"] != expected_total:
        raise ValueError("provider-reported token total does not add up")
    budget = evidence.get("execution_budget")
    if not isinstance(budget, dict):
        raise ValueError("execution budget is missing")
    _verify_budget(budget)
    budget_usage = budget["usage"]
    if (
        budget_usage["llm_call_count"] != operations["llm_call_count"]
        or budget_usage["data_provider_batch_count"]
        != operations["data_provider_batch_count"]
        or budget_usage["tool_call_count"] != operations["tool_call_count"]
        or budget_usage["reported_token_call_count"] != usage["reported_calls"]
        or budget_usage["reported_total_tokens"] != usage["total_tokens"]
    ):
        raise ValueError("execution and budget counts differ")
    return {
        "provider_reported_usage_complete": (
            usage["completeness"] == "complete"
            and usage["reported_calls"] >= 1
            and _is_int(usage["total_tokens"])
            and usage["total_tokens"] > 0
        ),
        "execution_budget_completed": (
            budget.get("status") == "succeeded"
            and budget.get("termination_reason") == "completed"
            and _is_int(budget["policy"]["max_reported_tokens"])
            and _is_int(usage["total_tokens"])
            and usage["total_tokens"]
            <= budget["policy"]["max_reported_tokens"]
        ),
    }


def verify_live_provider_acceptance(
    receipt_path: Path,
    observation_path: Path,
    quality_path: Path | None,
) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    canonical = deepcopy(receipt)
    observed_sha = canonical.pop("artifact_sha256", None)
    if observed_sha != _sha(canonical):
        raise ValueError("live-provider acceptance SHA-256 mismatch")
    if receipt.get("schema_id") != "bj-pal.live-provider-acceptance":
        raise ValueError("unexpected live-provider acceptance schema")
    if receipt.get("schema_version") != 1:
        raise ValueError("unsupported live-provider acceptance schema version")
    if receipt.get("classification") != (
        "operator_observed_bounded_live_provider_acceptance"
    ):
        raise ValueError("unexpected live-provider classification")
    if receipt.get("evidence_level") != (
        "configured_client_usage_and_quality_not_signed_provider_receipt"
    ):
        raise ValueError("live-provider evidence level overclaims provenance")
    datetime.fromisoformat(str(receipt.get("observed_at")).replace("Z", "+00:00"))

    observation = verify_live_model_observation(observation_path)
    if quality_path is None:
        quality = None
    else:
        quality = verify_live_plan_quality(quality_path, observation_path)
    linked = receipt.get("linked_artifacts")
    if not isinstance(linked, dict) or set(linked) != {
        "live_model_observation_sha256",
        "live_plan_quality_sha256",
    }:
        raise ValueError("linked live artifacts are incomplete")
    if linked["live_model_observation_sha256"] != observation["artifact_sha256"]:
        raise ValueError("linked live-model observation differs")
    quality_sha = quality["artifact_sha256"] if quality is not None else None
    if linked["live_plan_quality_sha256"] != quality_sha:
        raise ValueError("linked live-plan quality differs")
    if (
        receipt.get("observed_at") != observation.get("observed_at")
        or receipt.get("scenario_id") != observation.get("scenario_id")
        or receipt.get("provider") != observation.get("provider")
    ):
        raise ValueError("receipt and live-model identity differ")

    provider = receipt["provider"]
    endpoint = urlsplit(provider["endpoint_origin"])
    if endpoint.scheme != "https" or not endpoint.hostname:
        raise ValueError("provider endpoint origin must use HTTPS")

    handoff = receipt.get("credential_handoff")
    expected_handoff = {
        "source_type": "csswitch_active_profile",
        "config_file_mode": "0600",
        "owner_uid_match": True,
        "regular_file": True,
        "symlink": False,
        "profile_template": "deepseek",
        "api_format": "anthropic",
        "explicit_cost_ack": True,
        "exact_credential_occurrences_in_linked_artifacts": 0,
    }
    if handoff != expected_handoff:
        raise ValueError("credential handoff preflight is not satisfied")

    execution = receipt.get("execution_evidence")
    if not isinstance(execution, dict):
        raise ValueError("execution evidence is missing")
    execution_checks = _verify_execution(execution)
    quality_gate = bool(
        quality is not None
        and quality.get("metrics", {}).get("hard_gate_pass") is True
        and quality.get("metrics", {}).get("not_evaluable_count") == 0
    )
    expected_checks = {
        "credential_preflight_pass": True,
        "live_result_accepted": observation["result"]["outcome"]
        in {"accepted", "accepted_after_repair"},
        "quality_hard_gate_pass": quality_gate,
        **execution_checks,
        "exact_credential_absent_from_linked_artifacts": True,
    }
    acceptance = receipt.get("acceptance")
    if not isinstance(acceptance, dict) or set(acceptance) != {"checks", "gate_pass"}:
        raise ValueError("acceptance decision is incomplete")
    checks = acceptance.get("checks")
    if not isinstance(checks, dict) or set(checks) != _EXPECTED_CHECKS:
        raise ValueError("acceptance checks are incomplete")
    if checks != expected_checks or acceptance.get("gate_pass") is not all(
        expected_checks.values()
    ):
        raise ValueError("acceptance decision does not match linked evidence")

    provenance = receipt.get("provenance")
    if provenance != {
        "external_call_attestation": "operator_observation_not_independently_verifiable",
        "provider_identity": "configured_client_not_signed_provider_receipt",
        "usage_origin": "provider_reported_through_configured_client",
    }:
        raise ValueError("live-provider provenance boundary is incomplete")
    limitations = receipt.get("limitations")
    if not isinstance(limitations, list) or len(limitations) < 5:
        raise ValueError("live-provider limitations are incomplete")
    _reject_forbidden_keys(receipt)
    return receipt
