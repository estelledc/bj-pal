"""Build a privacy-minimized record of one explicitly authorized live call.

This module records configured-client evidence, not a signed provider receipt.
It deliberately excludes the prompt, raw model response, credentials, and plan.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlsplit


SCHEMA_ID = "bj-pal.live-model-observation"
SCHEMA_VERSION = 1
CLASSIFICATION = "operator_observed_live_provider_smoke"
EVIDENCE_LEVEL = "configured_client_observation_not_signed_provider_receipt"


def _sha(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def endpoint_origin(base_url: str) -> str:
    """Return only scheme + authority; reject credentials and non-TLS URLs."""
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("live model endpoint must be an HTTPS URL without credentials")
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"https://{parsed.hostname}{port}"


def build_live_model_observation(
    *,
    observed_at: str,
    scenario_id: str,
    provider: str,
    model: str,
    endpoint_base_url: str,
    execution_limits: Mapping[str, int],
    outcome: str,
    elapsed_ms: float,
    model_output_contract: Mapping[str, Any],
    recording_method: str = "runner_generated",
) -> dict[str, Any]:
    """Build one self-hashed observation without retaining request/response text."""
    datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if not scenario_id or not provider or not model:
        raise ValueError("scenario, provider, and model must be non-empty")
    if outcome not in {"accepted", "accepted_after_repair", "rejected"}:
        raise ValueError("unsupported live model outcome")
    if isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, (int, float)):
        raise ValueError("elapsed_ms must be numeric")
    if elapsed_ms < 0:
        raise ValueError("elapsed_ms must not be negative")

    snapshot = dict(model_output_contract)
    if snapshot.get("status") != outcome:
        raise ValueError("outcome must agree with model-output snapshot status")
    rejected = outcome == "rejected"
    artifact: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "classification": CLASSIFICATION,
        "evidence_level": EVIDENCE_LEVEL,
        "observed_at": observed_at,
        "scenario_id": scenario_id,
        "provider": {
            "configured_client": provider,
            "configured_model": model,
            "endpoint_origin": endpoint_origin(endpoint_base_url),
        },
        "execution_limits": dict(execution_limits),
        "result": {
            "outcome": outcome,
            "elapsed_ms": round(float(elapsed_ms), 3),
            "error_code": "invalid_model_output" if rejected else None,
            "fail_closed": rejected,
            "model_output_contract": snapshot,
        },
        "privacy": {
            "credential_persisted": False,
            "raw_prompt_persisted": False,
            "raw_model_output_persisted": False,
            "generated_plan_persisted": False,
        },
        "provenance": {
            "recording_method": recording_method,
            "external_call_attestation": "operator_observation_not_independently_verifiable",
            "provider_identity": "configured_client_not_signed_provider_receipt",
        },
        "limitations": [
            "One smoke sample is not a provider quality, repair, or failure rate.",
            "The artifact cannot independently prove that the external call occurred.",
            "Provider and model identity come from local client configuration, not a signed receipt.",
            "No prompt, raw response, generated plan, credential, or billable currency cost is retained.",
        ],
    }
    artifact["artifact_sha256"] = _sha(artifact)
    return artifact
