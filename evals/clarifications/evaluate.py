"""Run labeled ambiguity cases through the durable continuation boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from application import (
    PlanRequest,
    PlanningClarificationRequired,
    PlanningPreflight,
)
from clarifications import (
    ClarificationContinuationService,
    ClarificationRepository,
    ClarificationResolutionConflict,
)


@dataclass(frozen=True)
class ClarificationCase:
    case_id: str
    delivery: str
    request_payload: dict[str, Any]
    expected_code: str
    expected_field: str
    expected_option_ids: tuple[str, ...]
    selected_option_id: str
    answer: str | None
    expected_effective_value: Any
    job_policy: dict[str, Any]

    def to_request(self) -> PlanRequest:
        return PlanRequest.from_dict(self.request_payload)


@dataclass(frozen=True)
class ClarificationGoldenSet:
    name: str
    classification: str
    label_basis: str
    cases: tuple[ClarificationCase, ...]
    sha256: str


def load_golden_set(path: Path) -> ClarificationGoldenSet:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported clarification golden-set schema")
    cases = tuple(_load_case(item) for item in payload.get("cases") or [])
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("clarification cases must be non-empty and uniquely identified")
    return ClarificationGoldenSet(
        name=str(payload["name"]),
        classification=str(payload["classification"]),
        label_basis=str(payload["label_basis"]),
        cases=cases,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def evaluate_clarification_continuations(
    golden: ClarificationGoldenSet,
    preflight: PlanningPreflight | None = None,
) -> dict[str, Any]:
    preflight = preflight or PlanningPreflight()
    raw_cases: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="bj-pal-clarification-eval-") as temp_dir:
        database = Path(temp_dir) / "clarifications.db"
        service = ClarificationContinuationService(
            repository=ClarificationRepository(database),
            ttl_seconds=600,
        )
        for case in golden.cases:
            request = case.to_request()
            error = _required_clarification(preflight, request)
            session = service.issue(
                request=request,
                error=error,
                delivery=case.delivery,
                job_policy=case.job_policy,
            )
            restored = ClarificationRepository(database).get(session.continuation_id)
            if restored is None:
                raise ValueError("issued clarification could not be restored")

            resolved_session, resolved_request = service.resolve_request(
                continuation_id=session.continuation_id,
                delivery=case.delivery,
                option_id=case.selected_option_id,
                answer=case.answer,
            )
            _, repeated_request = service.resolve_request(
                continuation_id=session.continuation_id,
                delivery=case.delivery,
                option_id=case.selected_option_id,
                answer=case.answer,
            )
            post_result = None
            post_error = None
            try:
                post_result = preflight.normalize(resolved_request)
            except PlanningClarificationRequired as exc:
                post_error = exc

            initial_code = str(error.decision.unresolved[0].code)
            initial_field = str(error.decision.unresolved[0].field)
            post_code = (
                str(post_error.decision.unresolved[0].code) if post_error is not None else None
            )
            post_field = (
                str(post_error.decision.unresolved[0].field) if post_error is not None else None
            )
            observed_effective = (
                _effective_value(post_result.request, case.expected_field)
                if post_result is not None
                else None
            )
            options_payload = [item.to_dict() for item in session.options]
            decision_evidence = {
                "request_sha256": session.request_sha256,
                "delivery": case.delivery,
                "requirements": error.decision.to_dict(),
                "constraints": (
                    error.constraints.to_dict() if error.constraints is not None else None
                ),
                "job_policy": case.job_policy,
                "options": options_payload,
            }
            request_sha_valid = session.request_sha256 == _sha256_json(request.to_dict())
            decision_sha_valid = session.decision_sha256 == _sha256_json(decision_evidence)
            resolution_payload = resolved_session.resolution_payload or {}
            completion_payload = {
                "status": "preflight_passed" if post_result is not None else "preflight_failed",
                "post_request_sha256": (
                    _sha256_json(post_result.request.to_dict()) if post_result else None
                ),
            }
            owner = f"eval-{case.case_id}"
            service.claim_execution(
                continuation_id=session.continuation_id,
                owner=owner,
            )
            service.complete(
                continuation_id=session.continuation_id,
                owner=owner,
                result_payload=completion_payload,
            )
            completed = ClarificationRepository(database).get(session.continuation_id)
            if completed is None:
                raise ValueError("completed clarification could not be restored")

            alternate = next(
                item for item in session.options if item.option_id != case.selected_option_id
            )
            alternate_error = None
            try:
                service.resolve_request(
                    continuation_id=session.continuation_id,
                    delivery=case.delivery,
                    option_id=alternate.option_id,
                    answer="海淀公园片区" if alternate.requires_answer else None,
                )
            except ClarificationResolutionConflict as exc:
                alternate_error = type(exc).__name__

            resolution_sha256 = _sha256_json(resolution_payload)
            resolved_request_sha256 = _sha256_json(resolved_request.to_dict())
            result_sha256 = _sha256_json(completion_payload)
            raw_cases.append(
                {
                    "case_id": case.case_id,
                    "delivery": case.delivery,
                    "job_policy": case.job_policy,
                    "original_request": request.to_dict(),
                    "request_sha256": session.request_sha256,
                    "initial_requirements": error.decision.to_dict(),
                    "initial_constraints": (
                        error.constraints.to_dict() if error.constraints is not None else None
                    ),
                    "decision_sha256": session.decision_sha256,
                    "options": options_payload,
                    "expected_code": case.expected_code,
                    "observed_code": initial_code,
                    "expected_field": case.expected_field,
                    "observed_field": initial_field,
                    "expected_option_ids": list(case.expected_option_ids),
                    "observed_option_ids": [item.option_id for item in session.options],
                    "selected_option_id": case.selected_option_id,
                    "answer": case.answer,
                    "resolution": resolution_payload,
                    "resolution_sha256": completed.resolution_sha256,
                    "resolved_request": resolved_request.to_dict(),
                    "resolved_request_sha256": completed.resolved_request_sha256,
                    "repeated_request": repeated_request.to_dict(),
                    "restored_request": restored.request_payload,
                    "restored_requirements": restored.decision_payload,
                    "restored_options": [item.to_dict() for item in restored.options],
                    "restored_job_policy": restored.job_policy,
                    "completion_result": completed.result_payload,
                    "result_sha256": completed.result_sha256,
                    "post_request": post_result.request.to_dict() if post_result else None,
                    "post_requirements": (
                        post_result.requirements.to_dict() if post_result else None
                    ),
                    "post_constraints": (
                        post_result.constraints.to_dict() if post_result else None
                    ),
                    "post_error": (
                        {
                            "code": post_code,
                            "field": post_field,
                        }
                        if post_error is not None
                        else None
                    ),
                    "expected_effective_value": case.expected_effective_value,
                    "observed_effective_value": observed_effective,
                    "request_fingerprint_valid": request_sha_valid,
                    "decision_fingerprint_valid": decision_sha_valid,
                    "continuation_hash_chain_valid": (
                        completed.resolution_sha256 == resolution_sha256
                        and completed.resolved_request_sha256
                        == resolved_request_sha256
                        and completed.result_sha256 == result_sha256
                    ),
                    "durable_restore_valid": (
                        restored.request_payload == request.to_dict()
                        and restored.decision_payload == error.decision.to_dict()
                        and [item.to_dict() for item in restored.options] == options_payload
                        and restored.job_policy == case.job_policy
                    ),
                    "option_contract_correct": (
                        tuple(item.option_id for item in session.options)
                        == case.expected_option_ids
                    ),
                    "one_step_resolution_success": post_result is not None,
                    "effective_value_correct": _same_value(
                        observed_effective,
                        case.expected_effective_value,
                    ),
                    "same_conflict_recurred": (
                        post_code == initial_code and post_field == initial_field
                    ),
                    "resolution_round_trip_stable": (
                        repeated_request.to_dict() == resolved_request.to_dict()
                        and PlanRequest.from_dict(resolved_request.to_dict()).to_dict()
                        == resolved_request.to_dict()
                    ),
                    "alternate_option_id": alternate.option_id,
                    "alternate_resolution_error": alternate_error,
                }
            )
    return {
        "case_count": len(raw_cases),
        "metrics": recompute_metrics(raw_cases),
        "raw_cases": raw_cases,
    }


def recompute_metrics(raw_cases: list[dict[str, Any]]) -> dict[str, float]:
    if not raw_cases:
        raise ValueError("clarification evaluation needs at least one raw case")
    count = len(raw_cases)
    return {
        "one_step_resolution_success_rate": _rate(
            sum(bool(item["one_step_resolution_success"]) for item in raw_cases), count
        ),
        "effective_value_accuracy_rate": _rate(
            sum(bool(item["effective_value_correct"]) for item in raw_cases), count
        ),
        "same_conflict_recurrence_rate": _rate(
            sum(bool(item["same_conflict_recurred"]) for item in raw_cases), count
        ),
        "decision_fingerprint_valid_rate": _rate(
            sum(bool(item["decision_fingerprint_valid"]) for item in raw_cases), count
        ),
        "request_fingerprint_valid_rate": _rate(
            sum(bool(item["request_fingerprint_valid"]) for item in raw_cases), count
        ),
        "continuation_hash_chain_valid_rate": _rate(
            sum(bool(item["continuation_hash_chain_valid"]) for item in raw_cases),
            count,
        ),
        "durable_restore_rate": _rate(
            sum(bool(item["durable_restore_valid"]) for item in raw_cases), count
        ),
        "option_contract_coverage_rate": _rate(
            sum(bool(item["option_contract_correct"]) for item in raw_cases), count
        ),
        "resolution_round_trip_rate": _rate(
            sum(bool(item["resolution_round_trip_stable"]) for item in raw_cases), count
        ),
        "alternate_resolution_conflict_detection_rate": _rate(
            sum(
                item["alternate_resolution_error"]
                == "ClarificationResolutionConflict"
                for item in raw_cases
            ),
            count,
        ),
    }


def _load_case(item: dict[str, Any]) -> ClarificationCase:
    delivery = str(item.get("delivery") or "sync")
    if delivery not in {"sync", "job"}:
        raise ValueError("clarification case delivery must be sync or job")
    option_ids = tuple(str(value) for value in item.get("expected_option_ids") or [])
    selected = str(item["selected_option_id"])
    if len(option_ids) < 2 or selected not in option_ids:
        raise ValueError("clarification case option contract is invalid")
    request_payload = dict(item["request"])
    PlanRequest.from_dict(request_payload)
    return ClarificationCase(
        case_id=str(item["case_id"]),
        delivery=delivery,
        request_payload=request_payload,
        expected_code=str(item["expected_code"]),
        expected_field=str(item["expected_field"]),
        expected_option_ids=option_ids,
        selected_option_id=selected,
        answer=(str(item["answer"]) if item.get("answer") is not None else None),
        expected_effective_value=item.get("expected_effective_value"),
        job_policy=dict(item.get("job_policy") or {}),
    )


def _required_clarification(
    preflight: PlanningPreflight,
    request: PlanRequest,
) -> PlanningClarificationRequired:
    try:
        preflight.normalize(request)
    except PlanningClarificationRequired as exc:
        return exc
    raise ValueError("golden clarification case did not require clarification")


def _effective_value(request: PlanRequest, field: str) -> Any:
    if field == "area_anchor":
        return request.area_anchor
    if field == "user_input":
        return request.user_input
    if field == "persona":
        return request.persona
    prefix = "preferences."
    if not field.startswith(prefix):
        raise ValueError(f"unsupported clarification field: {field}")
    return getattr(request.preferences, field[len(prefix) :])


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


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0
