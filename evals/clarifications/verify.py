"""Independently verify clarification evidence and recompute its metrics."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .evaluate import load_golden_set, recompute_metrics


def canonical_artifact_sha256(payload: dict[str, Any]) -> str:
    canonical_payload = deepcopy(payload)
    canonical_payload.pop("artifact_sha256", None)
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_clarification_artifact(artifact_path: Path, golden_path: Path) -> dict:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported clarification artifact schema")
    if artifact.get("artifact_sha256") != canonical_artifact_sha256(artifact):
        raise ValueError("clarification artifact SHA-256 mismatch")

    golden = load_golden_set(golden_path)
    recorded_golden = artifact.get("golden_set") or {}
    if recorded_golden.get("sha256") != golden.sha256:
        raise ValueError("clarification golden-set SHA-256 mismatch")
    if recorded_golden.get("case_count") != len(golden.cases):
        raise ValueError("clarification golden-set case count mismatch")

    result = artifact.get("result") or {}
    raw_cases = result.get("raw_cases") or []
    if result.get("case_count") != len(golden.cases) or len(raw_cases) != len(golden.cases):
        raise ValueError("clarification result case count mismatch")
    by_id = {item.get("case_id"): item for item in raw_cases}
    if len(by_id) != len(raw_cases):
        raise ValueError("clarification raw case IDs must be unique")

    for case in golden.cases:
        observed = by_id.get(case.case_id)
        if observed is None:
            raise ValueError(f"missing clarification raw case: {case.case_id}")
        _verify_case(case, observed)

    metrics = recompute_metrics(raw_cases)
    if result.get("metrics") != metrics:
        raise ValueError("clarification metrics do not match raw cases")
    return artifact


def _verify_case(case, observed: dict[str, Any]) -> None:
    if observed.get("delivery") != case.delivery:
        raise ValueError(f"clarification delivery label mismatch: {case.case_id}")
    if observed.get("job_policy") != case.job_policy:
        raise ValueError(f"clarification job policy mismatch: {case.case_id}")
    if observed.get("original_request") != case.to_request().to_dict():
        raise ValueError(f"clarification request label mismatch: {case.case_id}")
    if observed.get("expected_code") != case.expected_code:
        raise ValueError(f"clarification code label mismatch: {case.case_id}")
    if observed.get("expected_field") != case.expected_field:
        raise ValueError(f"clarification field label mismatch: {case.case_id}")
    if observed.get("expected_option_ids") != list(case.expected_option_ids):
        raise ValueError(f"clarification option label mismatch: {case.case_id}")
    if observed.get("selected_option_id") != case.selected_option_id:
        raise ValueError(f"clarification selection label mismatch: {case.case_id}")
    if observed.get("answer") != case.answer:
        raise ValueError(f"clarification answer label mismatch: {case.case_id}")
    if not _same_value(
        observed.get("expected_effective_value"),
        case.expected_effective_value,
    ):
        raise ValueError(f"clarification effective label mismatch: {case.case_id}")

    original_request = observed["original_request"]
    request_sha256 = _sha256_json(original_request)
    if observed.get("request_sha256") != request_sha256:
        raise ValueError(f"clarification request fingerprint mismatch: {case.case_id}")
    if observed.get("request_fingerprint_valid") is not True:
        raise ValueError(f"clarification request fingerprint flag mismatch: {case.case_id}")

    options = observed.get("options") or []
    option_ids = [item.get("option_id") for item in options]
    if option_ids != list(case.expected_option_ids):
        raise ValueError(f"clarification observed options mismatch: {case.case_id}")
    if observed.get("observed_option_ids") != option_ids:
        raise ValueError(f"clarification option evidence mismatch: {case.case_id}")
    if observed.get("option_contract_correct") is not True:
        raise ValueError(f"clarification option contract flag mismatch: {case.case_id}")

    initial_requirements = observed.get("initial_requirements") or {}
    unresolved = initial_requirements.get("unresolved") or []
    if not unresolved:
        raise ValueError(f"clarification unresolved evidence missing: {case.case_id}")
    if observed.get("observed_code") != unresolved[0].get("code"):
        raise ValueError(f"clarification observed code mismatch: {case.case_id}")
    if observed.get("observed_field") != unresolved[0].get("field"):
        raise ValueError(f"clarification observed field mismatch: {case.case_id}")
    if observed.get("observed_code") != case.expected_code:
        raise ValueError(f"clarification code accuracy mismatch: {case.case_id}")
    if observed.get("observed_field") != case.expected_field:
        raise ValueError(f"clarification field accuracy mismatch: {case.case_id}")

    decision_evidence = {
        "request_sha256": request_sha256,
        "delivery": case.delivery,
        "requirements": initial_requirements,
        "constraints": observed.get("initial_constraints"),
        "job_policy": case.job_policy,
        "options": options,
    }
    decision_sha256 = _sha256_json(decision_evidence)
    if observed.get("decision_sha256") != decision_sha256:
        raise ValueError(f"clarification decision fingerprint mismatch: {case.case_id}")
    if observed.get("decision_fingerprint_valid") is not True:
        raise ValueError(f"clarification decision fingerprint flag mismatch: {case.case_id}")

    selected = next(
        (item for item in options if item.get("option_id") == case.selected_option_id),
        None,
    )
    if selected is None:
        raise ValueError(f"clarification selected option missing: {case.case_id}")
    resolution = observed.get("resolution") or {}
    if resolution.get("decision_sha256") != decision_sha256:
        raise ValueError(f"clarification resolution fingerprint mismatch: {case.case_id}")
    if resolution.get("code") != case.expected_code:
        raise ValueError(f"clarification resolution code mismatch: {case.case_id}")
    if resolution.get("field") != case.expected_field:
        raise ValueError(f"clarification resolution field mismatch: {case.case_id}")
    if resolution.get("option_id") != case.selected_option_id:
        raise ValueError(f"clarification resolution option mismatch: {case.case_id}")
    expected_resolution_value = case.answer if selected.get("requires_answer") else selected.get("value")
    if not _same_value(resolution.get("value"), expected_resolution_value):
        raise ValueError(f"clarification resolution value mismatch: {case.case_id}")
    if observed.get("resolution_sha256") != _sha256_json(resolution):
        raise ValueError(f"clarification resolution hash mismatch: {case.case_id}")

    resolved_request = observed.get("resolved_request") or {}
    resolutions = resolved_request.get("resolutions") or []
    if not resolutions or resolutions[-1] != resolution:
        raise ValueError(f"clarification resolved request mismatch: {case.case_id}")
    if observed.get("resolved_request_sha256") != _sha256_json(resolved_request):
        raise ValueError(f"clarification resolved request hash mismatch: {case.case_id}")
    if observed.get("repeated_request") != resolved_request:
        raise ValueError(f"clarification idempotent replay mismatch: {case.case_id}")
    if observed.get("resolution_round_trip_stable") is not True:
        raise ValueError(f"clarification round-trip flag mismatch: {case.case_id}")

    durable_valid = (
        observed.get("restored_request") == original_request
        and observed.get("restored_requirements") == initial_requirements
        and observed.get("restored_options") == options
        and observed.get("restored_job_policy") == case.job_policy
    )
    if observed.get("durable_restore_valid") is not durable_valid or not durable_valid:
        raise ValueError(f"clarification durable restore mismatch: {case.case_id}")

    completion_result = observed.get("completion_result") or {}
    if observed.get("result_sha256") != _sha256_json(completion_result):
        raise ValueError(f"clarification result hash mismatch: {case.case_id}")
    if completion_result.get("status") != "preflight_passed":
        raise ValueError(f"clarification completion status mismatch: {case.case_id}")
    if completion_result.get("post_request_sha256") != _sha256_json(
        observed.get("post_request") or {}
    ):
        raise ValueError(f"clarification completion request mismatch: {case.case_id}")
    hash_chain_valid = all(
        (
            observed.get("request_fingerprint_valid"),
            observed.get("decision_fingerprint_valid"),
            observed.get("resolution_sha256") == _sha256_json(resolution),
            observed.get("resolved_request_sha256") == _sha256_json(resolved_request),
            observed.get("result_sha256") == _sha256_json(completion_result),
        )
    )
    if observed.get("continuation_hash_chain_valid") is not hash_chain_valid:
        raise ValueError(f"clarification hash chain flag mismatch: {case.case_id}")

    post_request = observed.get("post_request") or {}
    post_requirements = observed.get("post_requirements") or {}
    post_constraints = observed.get("post_constraints") or {}
    one_step_success = (
        observed.get("post_error") is None
        and bool(post_request)
        and post_requirements.get("status") != "clarification_required"
        and not (post_constraints.get("conflicts") or [])
    )
    if observed.get("one_step_resolution_success") is not one_step_success:
        raise ValueError(f"clarification one-step outcome mismatch: {case.case_id}")
    effective = _effective_value(post_request, case.expected_field)
    if not _same_value(observed.get("observed_effective_value"), effective):
        raise ValueError(f"clarification observed effective mismatch: {case.case_id}")
    effective_correct = _same_value(effective, case.expected_effective_value)
    if observed.get("effective_value_correct") is not effective_correct:
        raise ValueError(f"clarification effective flag mismatch: {case.case_id}")
    if observed.get("same_conflict_recurred") is not False:
        raise ValueError(f"clarification conflict recurred: {case.case_id}")
    if observed.get("alternate_resolution_error") != "ClarificationResolutionConflict":
        raise ValueError(f"clarification alternate answer was not fenced: {case.case_id}")
    if observed.get("alternate_option_id") == case.selected_option_id:
        raise ValueError(f"clarification alternate option mismatch: {case.case_id}")


def _effective_value(request: dict[str, Any], field: str) -> Any:
    if field in {"area_anchor", "user_input", "persona"}:
        return request.get(field)
    prefix = "preferences."
    if not field.startswith(prefix):
        raise ValueError(f"unsupported clarification field: {field}")
    return (request.get("preferences") or {}).get(field[len(prefix) :])


def _sha256_json(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) < 1e-9
    return left == right
