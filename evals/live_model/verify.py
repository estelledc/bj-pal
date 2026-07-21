"""Independent verifier for privacy-minimized live-model observations.

The verifier intentionally does not import the production builder or model
contract implementation. It validates integrity and semantics, but cannot
prove that an external request occurred.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_LIMIT_FIELDS = {
    "max_output_tokens",
    "max_llm_calls",
    "max_data_provider_batches",
    "max_tool_calls",
    "max_transport_attempts_per_llm_call",
    "max_reported_tokens",
    "max_wall_clock_ms",
}
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "password",
    "prompt",
    "raw_model_output",
    "raw_output",
    "request_body",
    "secret",
    "token",
    "user_input",
}
_EXPECTED_PRO_SUITE_SCENARIOS = {
    "synthetic-friends-sanlitun-3h-budget",
    "synthetic-family-wudaoying-4h-child-diet",
    "synthetic-solo-798-3h-indoor",
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


def _verify_model_snapshot(snapshot: dict[str, Any], expected_status: str) -> None:
    canonical = deepcopy(snapshot)
    observed = canonical.pop("artifact_sha256", None)
    if snapshot.get("version") != "model_output_contract_v1" or observed != _sha(canonical):
        raise ValueError("model-output snapshot integrity mismatch")
    if snapshot.get("status") != expected_status:
        raise ValueError("model-output snapshot status mismatch")
    attempts = snapshot.get("attempt_count")
    repaired = snapshot.get("repair_attempted")
    candidates = snapshot.get("candidate_count")
    issues = snapshot.get("issue_codes")
    if attempts not in {1, 2} or repaired is not (attempts == 2):
        raise ValueError("model-output attempt semantics mismatch")
    if not _is_int(candidates) or candidates < 1:
        raise ValueError("candidate_count must be positive")
    if not isinstance(issues, list) or issues != sorted(set(issues)):
        raise ValueError("issue codes must be a canonical list")
    if not all(isinstance(item, str) and item for item in issues):
        raise ValueError("issue codes must be non-empty strings")
    if expected_status == "accepted" and (attempts != 1 or issues):
        raise ValueError("initial acceptance semantics mismatch")
    if expected_status in {"accepted_after_repair", "rejected"} and (
        attempts != 2 or not issues
    ):
        raise ValueError("repair/rejection semantics mismatch")


def verify_live_model_observation(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    canonical = deepcopy(artifact)
    observed_sha = canonical.pop("artifact_sha256", None)
    if observed_sha != _sha(canonical):
        raise ValueError("live-model observation SHA-256 mismatch")
    if artifact.get("schema_id") != "bj-pal.live-model-observation":
        raise ValueError("unexpected live-model schema")
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported live-model schema version")
    if artifact.get("classification") != "operator_observed_live_provider_smoke":
        raise ValueError("unexpected live-model classification")
    if artifact.get("evidence_level") != "configured_client_observation_not_signed_provider_receipt":
        raise ValueError("live-model evidence level overclaims provenance")
    datetime.fromisoformat(str(artifact.get("observed_at")).replace("Z", "+00:00"))
    if not isinstance(artifact.get("scenario_id"), str) or not artifact["scenario_id"]:
        raise ValueError("scenario_id must be non-empty")

    provider = artifact.get("provider")
    if not isinstance(provider, dict):
        raise ValueError("provider metadata is missing")
    for key in ("configured_client", "configured_model", "endpoint_origin"):
        if not isinstance(provider.get(key), str) or not provider[key]:
            raise ValueError("provider metadata is incomplete")
    endpoint = urlsplit(provider["endpoint_origin"])
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.path not in {"", "/"}
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("endpoint must contain only an HTTPS origin")

    limits = artifact.get("execution_limits")
    if not isinstance(limits, dict) or set(limits) != _LIMIT_FIELDS:
        raise ValueError("execution limits are incomplete")
    if any(not _is_int(value) or value < 1 for value in limits.values()):
        raise ValueError("execution limits must be positive integers")

    result = artifact.get("result")
    if not isinstance(result, dict):
        raise ValueError("live-model result is missing")
    outcome = result.get("outcome")
    if outcome not in {"accepted", "accepted_after_repair", "rejected"}:
        raise ValueError("unsupported live-model outcome")
    elapsed = result.get("elapsed_ms")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
        raise ValueError("elapsed_ms must be non-negative")
    rejected = outcome == "rejected"
    if result.get("error_code") != ("invalid_model_output" if rejected else None):
        raise ValueError("error_code does not match outcome")
    if result.get("fail_closed") is not rejected:
        raise ValueError("fail_closed does not match outcome")
    snapshot = result.get("model_output_contract")
    if not isinstance(snapshot, dict):
        raise ValueError("model-output snapshot is missing")
    _verify_model_snapshot(snapshot, outcome)

    privacy = artifact.get("privacy")
    if not isinstance(privacy, dict) or not privacy:
        raise ValueError("privacy declaration is missing")
    if any(value is not False for value in privacy.values()):
        raise ValueError("live-model artifact claims persisted private material")
    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict) or (
        provenance.get("external_call_attestation")
        != "operator_observation_not_independently_verifiable"
        or provenance.get("provider_identity")
        != "configured_client_not_signed_provider_receipt"
    ):
        raise ValueError("live-model provenance boundary is incomplete")
    limitations = artifact.get("limitations")
    if not isinstance(limitations, list) or len(limitations) < 4:
        raise ValueError("live-model limitations are incomplete")
    _reject_forbidden_keys(artifact)
    return artifact


def verify_live_model_pair(
    flash_path: Path,
    pro_path: Path,
) -> dict[str, Any]:
    """Verify a same-scenario two-sample comparison without inventing rates."""
    flash = verify_live_model_observation(flash_path)
    pro = verify_live_model_observation(pro_path)
    if flash["scenario_id"] != pro["scenario_id"]:
        raise ValueError("live-model comparison scenarios differ")
    if flash["provider"]["configured_client"] != pro["provider"]["configured_client"]:
        raise ValueError("live-model comparison clients differ")
    if flash["provider"]["endpoint_origin"] != pro["provider"]["endpoint_origin"]:
        raise ValueError("live-model comparison endpoint origins differ")
    if flash["execution_limits"] != pro["execution_limits"]:
        raise ValueError("live-model comparison execution limits differ")
    flash_snapshot = flash["result"]["model_output_contract"]
    pro_snapshot = pro["result"]["model_output_contract"]
    if flash_snapshot["candidate_count"] != pro_snapshot["candidate_count"]:
        raise ValueError("live-model comparison candidate counts differ")
    if flash["provider"]["configured_model"] != "deepseek-v4-flash":
        raise ValueError("comparison flash sample has an unexpected model")
    if pro["provider"]["configured_model"] != "deepseek-v4-pro":
        raise ValueError("comparison pro sample has an unexpected model")
    if (
        flash["result"]["outcome"] != "rejected"
        or flash_snapshot["attempt_count"] != 2
        or pro["result"]["outcome"] != "accepted"
        or pro_snapshot["attempt_count"] != 1
    ):
        raise ValueError("live-model comparison outcome semantics differ from evidence")
    return {
        "classification": "two_single_sample_model_selection_signal",
        "scenario_id": flash["scenario_id"],
        "candidate_count": flash_snapshot["candidate_count"],
        "samples": [
            {
                "model": flash["provider"]["configured_model"],
                "outcome": flash["result"]["outcome"],
                "attempt_count": flash_snapshot["attempt_count"],
                "elapsed_ms": flash["result"]["elapsed_ms"],
                "artifact_sha256": flash["artifact_sha256"],
            },
            {
                "model": pro["provider"]["configured_model"],
                "outcome": pro["result"]["outcome"],
                "attempt_count": pro_snapshot["attempt_count"],
                "elapsed_ms": pro["result"]["elapsed_ms"],
                "artifact_sha256": pro["artifact_sha256"],
            },
        ],
        "decision": "prefer_pro_for_next_bounded_live_trials",
        "limitations": [
            "Each model has exactly one operator-observed sample.",
            "The comparison does not estimate success rate or latency distribution.",
            "Configured model identity is not a signed provider receipt.",
        ],
    }


def verify_live_model_suite(paths: list[Path] | tuple[Path, ...]) -> dict[str, Any]:
    """Verify the fixed three-case Pro suite and return counts, never rates."""
    observations = [verify_live_model_observation(path) for path in paths]
    scenario_ids = [item["scenario_id"] for item in observations]
    if len(observations) != 3 or set(scenario_ids) != _EXPECTED_PRO_SUITE_SCENARIOS:
        raise ValueError("live-model suite scenario set is incomplete or duplicated")
    if len({item["artifact_sha256"] for item in observations}) != len(observations):
        raise ValueError("live-model suite artifacts must be distinct")
    clients = {item["provider"]["configured_client"] for item in observations}
    models = {item["provider"]["configured_model"] for item in observations}
    origins = {item["provider"]["endpoint_origin"] for item in observations}
    if clients != {"dpsk"} or models != {"deepseek-v4-pro"} or len(origins) != 1:
        raise ValueError("live-model suite provider configuration differs")
    first_limits = observations[0]["execution_limits"]
    if any(item["execution_limits"] != first_limits for item in observations[1:]):
        raise ValueError("live-model suite execution limits differ")

    ordered = sorted(observations, key=lambda item: item["scenario_id"])
    accepted_count = sum(item["result"]["outcome"] == "accepted" for item in ordered)
    first_pass_count = sum(
        item["result"]["outcome"] == "accepted"
        and item["result"]["model_output_contract"]["attempt_count"] == 1
        for item in ordered
    )
    elapsed = sorted(float(item["result"]["elapsed_ms"]) for item in ordered)
    cases = [
        {
            "scenario_id": item["scenario_id"],
            "outcome": item["result"]["outcome"],
            "attempt_count": item["result"]["model_output_contract"]["attempt_count"],
            "candidate_count": item["result"]["model_output_contract"]["candidate_count"],
            "elapsed_ms": item["result"]["elapsed_ms"],
            "artifact_sha256": item["artifact_sha256"],
        }
        for item in ordered
    ]
    return {
        "classification": "three_fixed_synthetic_live_acceptance_cases",
        "model": "deepseek-v4-pro",
        "case_count": len(cases),
        "accepted_count": accepted_count,
        "first_pass_count": first_pass_count,
        "gate_pass": accepted_count == len(cases) and first_pass_count == len(cases),
        "elapsed_ms": {
            "min": elapsed[0],
            "median": elapsed[1],
            "max": elapsed[2],
        },
        "candidate_count_range": {
            "min": min(item["candidate_count"] for item in cases),
            "max": max(item["candidate_count"] for item in cases),
        },
        "cases": cases,
        "limitations": [
            "These are three fixed synthetic scenarios, not a sampled production distribution.",
            "Each scenario was run once, so counts are not a success-rate estimate.",
            "The smallest candidate pool contains only two candidates.",
            "Configured model identity is not a signed provider receipt.",
            "No raw output, plan-quality judgment, user outcome, or currency cost is retained.",
        ],
    }
