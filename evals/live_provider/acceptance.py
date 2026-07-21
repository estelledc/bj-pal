"""Build a payload-minimized receipt for one bounded live-provider run."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping


SCHEMA_ID = "bj-pal.live-provider-acceptance"
SCHEMA_VERSION = 1
CLASSIFICATION = "operator_observed_bounded_live_provider_acceptance"
EVIDENCE_LEVEL = "configured_client_usage_and_quality_not_signed_provider_receipt"


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _verify_embedded_sha(payload: Mapping[str, Any], field: str) -> None:
    canonical = deepcopy(dict(payload))
    observed = canonical.pop("artifact_sha256", None)
    if observed != _sha(canonical):
        raise ValueError(f"{field} integrity mismatch")


def _execution_projection(execution: Mapping[str, Any]) -> dict[str, Any]:
    _verify_embedded_sha(execution, "execution observation")
    if execution.get("version") != "execution_observation_v2":
        raise ValueError("live acceptance requires execution_observation_v2")
    if execution.get("status") != "succeeded":
        raise ValueError("live acceptance requires a succeeded execution")
    operations = execution.get("operation_counts")
    token_usage = execution.get("token_usage")
    budget = execution.get("execution_budget")
    if not isinstance(operations, dict) or not isinstance(token_usage, dict):
        raise ValueError("execution operation/token evidence is missing")
    if not isinstance(budget, dict):
        raise ValueError("execution budget evidence is missing")
    _verify_embedded_sha(budget, "execution budget")

    input_tokens = token_usage.get("input_tokens")
    output_tokens = token_usage.get("output_tokens")
    reported_calls = _nonnegative_int(
        token_usage.get("reported_calls"), "reported_calls"
    )
    if input_tokens is not None:
        input_tokens = _nonnegative_int(input_tokens, "input_tokens")
    if output_tokens is not None:
        output_tokens = _nonnegative_int(output_tokens, "output_tokens")
    total_tokens = (
        (input_tokens or 0) + (output_tokens or 0)
        if input_tokens is not None or output_tokens is not None
        else None
    )
    budget_usage = budget.get("usage")
    if not isinstance(budget_usage, dict):
        raise ValueError("execution budget usage is missing")
    if budget_usage.get("reported_total_tokens") != total_tokens:
        raise ValueError("budget and token totals differ")
    if budget_usage.get("reported_token_call_count") != reported_calls:
        raise ValueError("budget and reported call totals differ")
    if budget_usage.get("llm_call_count") != operations.get("llm_call_count"):
        raise ValueError("budget and LLM call totals differ")

    return {
        "source_artifact_sha256": execution["artifact_sha256"],
        "version": execution["version"],
        "status": execution["status"],
        "duration_ms": execution.get("duration_ms"),
        "operation_counts": {
            "llm_call_count": operations.get("llm_call_count"),
            "data_provider_batch_count": operations.get(
                "data_provider_batch_count"
            ),
            "tool_call_count": operations.get("tool_call_count"),
        },
        "provider_reported_usage": {
            "completeness": token_usage.get("completeness"),
            "reported_calls": reported_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        },
        "execution_budget": deepcopy(budget),
    }


def build_live_provider_acceptance(
    *,
    observation: Mapping[str, Any],
    quality: Mapping[str, Any] | None,
    execution: Mapping[str, Any],
    credential_metadata: Mapping[str, Any],
    credential_value: str,
    explicit_cost_ack: bool,
) -> dict[str, Any]:
    """Bind credential preflight, live result, usage, and quality evidence."""
    if not explicit_cost_ack:
        raise ValueError("live provider acceptance requires explicit cost acknowledgement")
    if not credential_value:
        raise ValueError("credential value is required only for in-memory leak checks")
    _verify_embedded_sha(observation, "live-model observation")
    if quality is not None:
        _verify_embedded_sha(quality, "live-plan quality")
    observed_at = observation.get("observed_at")
    datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))

    rendered_sources = json.dumps(
        {"observation": observation, "quality": quality},
        ensure_ascii=False,
        sort_keys=True,
    )
    exact_credential_occurrences = rendered_sources.count(credential_value)
    if exact_credential_occurrences:
        raise ValueError("credential value appeared in a persisted live artifact")

    execution_projection = _execution_projection(execution)
    token_usage = execution_projection["provider_reported_usage"]
    budget = execution_projection["execution_budget"]
    quality_gate = bool(
        quality is not None
        and quality.get("metrics", {}).get("hard_gate_pass") is True
        and quality.get("metrics", {}).get("not_evaluable_count") == 0
    )
    outcome = observation.get("result", {}).get("outcome")
    credential_gate = dict(credential_metadata) == {
        "source_type": "csswitch_active_profile",
        "config_file_mode": "0600",
        "owner_uid_match": True,
        "regular_file": True,
        "symlink": False,
        "profile_template": "deepseek",
        "api_format": "anthropic",
    }
    reported_usage_gate = (
        token_usage["completeness"] == "complete"
        and token_usage["reported_calls"] >= 1
        and isinstance(token_usage["total_tokens"], int)
        and token_usage["total_tokens"] > 0
    )
    budget_policy = budget.get("policy") or {}
    budget_usage = budget.get("usage") or {}
    budget_gate = (
        budget.get("status") == "succeeded"
        and budget.get("termination_reason") == "completed"
        and budget_usage.get("reported_total_tokens")
        == token_usage["total_tokens"]
        and isinstance(budget_policy.get("max_reported_tokens"), int)
        and token_usage["total_tokens"] <= budget_policy["max_reported_tokens"]
    )
    checks = {
        "credential_preflight_pass": credential_gate,
        "live_result_accepted": outcome in {"accepted", "accepted_after_repair"},
        "quality_hard_gate_pass": quality_gate,
        "provider_reported_usage_complete": reported_usage_gate,
        "execution_budget_completed": budget_gate,
        "exact_credential_absent_from_linked_artifacts": (
            exact_credential_occurrences == 0
        ),
    }

    artifact: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "classification": CLASSIFICATION,
        "evidence_level": EVIDENCE_LEVEL,
        "observed_at": observed_at,
        "scenario_id": observation.get("scenario_id"),
        "provider": deepcopy(observation.get("provider")),
        "credential_handoff": {
            **dict(credential_metadata),
            "explicit_cost_ack": True,
            "exact_credential_occurrences_in_linked_artifacts": (
                exact_credential_occurrences
            ),
        },
        "linked_artifacts": {
            "live_model_observation_sha256": observation.get("artifact_sha256"),
            "live_plan_quality_sha256": (
                quality.get("artifact_sha256") if quality is not None else None
            ),
        },
        "execution_evidence": execution_projection,
        "acceptance": {
            "checks": checks,
            "gate_pass": all(checks.values()),
        },
        "provenance": {
            "external_call_attestation": (
                "operator_observation_not_independently_verifiable"
            ),
            "provider_identity": "configured_client_not_signed_provider_receipt",
            "usage_origin": "provider_reported_through_configured_client",
        },
        "limitations": [
            "This is one operator-observed fixed synthetic scenario, not a success-rate estimate.",
            "Configured provider/model identity and external execution are not backed by a signed provider receipt.",
            "Token counts are provider-reported through the configured client; no invoice or currency cost is claimed.",
            "The credential absence check covers only the generated observation and quality artifacts.",
            "No prompt, raw response, generated plan, credential, profile identifier, or local path is retained.",
        ],
    }
    artifact["artifact_sha256"] = _sha(artifact)
    return artifact
