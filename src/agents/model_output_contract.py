"""Strict, candidate-bound contract for Planner model output.

This module validates untrusted LLM output before it becomes a ``Plan``.  It
does not silently coerce types, drop unknown fields, accept locally recovered
partial JSON, or allow POIs outside the exact request candidate set.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


MODEL_OUTPUT_CONTRACT_VERSION = "model_output_contract_v1"
ModelOutputStatus = Literal["accepted", "accepted_after_repair", "rejected"]
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_FOOD_ONLY_STEP_KINDS = frozenset({"meal", "snack"})


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ModelOutputIssue:
    code: str
    path: str


class ModelOutputValidationError(ValueError):
    """Internal validation failure containing no raw model values."""

    def __init__(self, issues: list[ModelOutputIssue] | tuple[ModelOutputIssue, ...]):
        normalized = tuple(issues)
        if not normalized:
            raise ValueError("model output validation requires at least one issue")
        self.issues = normalized
        super().__init__(", ".join(f"{item.path}:{item.code}" for item in normalized))

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return tuple(sorted({item.code for item in self.issues}))

    def repair_hints(self) -> list[dict[str, str]]:
        return [{"code": item.code, "path": item.path} for item in self.issues]


@dataclass(frozen=True)
class ModelOutputContractSnapshot:
    version: str
    status: ModelOutputStatus
    attempt_count: int
    repair_attempted: bool
    candidate_count: int
    issue_codes: tuple[str, ...]
    artifact_sha256: str

    @classmethod
    def create(
        cls,
        *,
        status: ModelOutputStatus,
        attempt_count: int,
        repair_attempted: bool,
        candidate_count: int,
        issue_codes: tuple[str, ...] | list[str] = (),
    ) -> "ModelOutputContractSnapshot":
        if isinstance(attempt_count, bool) or not isinstance(attempt_count, int):
            raise ValueError("attempt_count must be an integer")
        if attempt_count not in (1, 2):
            raise ValueError("attempt_count must be 1 or 2")
        if not isinstance(repair_attempted, bool):
            raise ValueError("repair_attempted must be boolean")
        if repair_attempted is not (attempt_count == 2):
            raise ValueError("repair_attempted must agree with attempt_count")
        if isinstance(candidate_count, bool) or not isinstance(candidate_count, int):
            raise ValueError("candidate_count must be an integer")
        if candidate_count < 1:
            raise ValueError("candidate_count must be positive")
        if any(not isinstance(item, str) or not item for item in issue_codes):
            raise ValueError("issue_codes must contain non-empty strings")
        normalized_codes = tuple(sorted(set(issue_codes)))
        if status == "accepted":
            if attempt_count != 1 or normalized_codes:
                raise ValueError("initial acceptance requires one clean attempt")
        elif status in {"accepted_after_repair", "rejected"}:
            if attempt_count != 2 or not normalized_codes:
                raise ValueError("repaired/rejected evidence requires two attempts and issues")
        else:
            raise ValueError("unsupported model output status")
        payload = {
            "version": MODEL_OUTPUT_CONTRACT_VERSION,
            "status": status,
            "attempt_count": attempt_count,
            "repair_attempted": repair_attempted,
            "candidate_count": candidate_count,
            "issue_codes": list(normalized_codes),
        }
        return cls(
            version=MODEL_OUTPUT_CONTRACT_VERSION,
            status=status,
            attempt_count=attempt_count,
            repair_attempted=repair_attempted,
            candidate_count=candidate_count,
            issue_codes=normalized_codes,
            artifact_sha256=_canonical_sha256(payload),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ModelOutputContractSnapshot":
        return cls(
            version=str(payload["version"]),
            status=payload["status"],
            attempt_count=int(payload["attempt_count"]),
            repair_attempted=bool(payload["repair_attempted"]),
            candidate_count=int(payload["candidate_count"]),
            issue_codes=tuple(str(item) for item in payload.get("issue_codes") or ()),
            artifact_sha256=str(payload["artifact_sha256"]),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issue_codes"] = list(self.issue_codes)
        return payload

    def verify_integrity(self) -> bool:
        payload = self.to_dict()
        observed = payload.pop("artifact_sha256")
        status_semantics_valid = (
            self.status == "accepted"
            and self.attempt_count == 1
            and not self.issue_codes
        ) or (
            self.status in {"accepted_after_repair", "rejected"}
            and self.attempt_count == 2
            and bool(self.issue_codes)
        )
        return (
            self.version == MODEL_OUTPUT_CONTRACT_VERSION
            and self.status in {"accepted", "accepted_after_repair", "rejected"}
            and isinstance(self.attempt_count, int)
            and not isinstance(self.attempt_count, bool)
            and self.attempt_count in (1, 2)
            and isinstance(self.repair_attempted, bool)
            and self.repair_attempted is (self.attempt_count == 2)
            and isinstance(self.candidate_count, int)
            and not isinstance(self.candidate_count, bool)
            and self.candidate_count >= 1
            and all(isinstance(item, str) and bool(item) for item in self.issue_codes)
            and status_semantics_valid
            and observed == _canonical_sha256(payload)
        )


class ModelOutputContractError(RuntimeError):
    """A bounded repair could not produce a safe Planner output."""

    code = "invalid_model_output"
    retryable = False

    def __init__(self, snapshot: ModelOutputContractSnapshot) -> None:
        if snapshot.status != "rejected" or not snapshot.verify_integrity():
            raise ValueError("model output rejection snapshot is invalid")
        self.snapshot = snapshot
        super().__init__("planner model output failed the strict contract")

    def safe_details(self) -> dict[str, Any]:
        return self.snapshot.to_dict()


class _StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _StepDraft(_StrictOutputModel):
    step_index: int = Field(ge=1, le=8)
    kind: Literal["citywalk", "meal", "culture", "rest", "shopping", "depart", "snack"]
    poi_id: str | None
    poi_name: str = Field(min_length=1, max_length=200)
    start_time: str
    duration_min: int = Field(ge=0, le=480)
    mode_to_here: Literal["walking", "bicycling", "driving", "transit"]
    rationale: str = Field(min_length=1, max_length=1000)

    @field_validator("start_time")
    @classmethod
    def validate_start_time(cls, value: str) -> str:
        if not _TIME_PATTERN.fullmatch(value):
            raise ValueError("start_time must be HH:MM")
        return value


class _PlanDraft(_StrictOutputModel):
    persona: Literal["family", "friends", "solo", "with_parents"]
    area_anchor: str = Field(min_length=1, max_length=200)
    steps: list[_StepDraft] = Field(min_length=2, max_length=8)
    fallback_strategies: dict[str, str]
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("fallback_strategies")
    @classmethod
    def validate_fallbacks(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 12:
            raise ValueError("too many fallback strategies")
        if any(not key.strip() or not item.strip() for key, item in value.items()):
            raise ValueError("fallback keys and values must be non-empty")
        return value


def validate_plan_payload(
    payload: Any,
    *,
    expected_persona: str,
    expected_area_anchor: str,
    candidate_names_by_id: Mapping[str, str],
    candidate_categories_by_id: Mapping[str, tuple[str, ...]],
) -> dict[str, Any]:
    """Return a normalized strict payload or privacy-minimized issue codes."""
    if not candidate_names_by_id:
        raise ValueError("candidate_names_by_id must not be empty")
    if any(
        not isinstance(candidate_id, str)
        or not candidate_id
        or not isinstance(candidate_name, str)
        or not candidate_name
        for candidate_id, candidate_name in candidate_names_by_id.items()
    ):
        raise ValueError("candidate_names_by_id must contain non-empty string pairs")
    if set(candidate_categories_by_id) != set(candidate_names_by_id) or any(
        not isinstance(categories, tuple)
        or not categories
        or any(not isinstance(category, str) or not category for category in categories)
        for categories in candidate_categories_by_id.values()
    ):
        raise ValueError(
            "candidate_categories_by_id must cover every candidate with non-empty string tuples"
        )
    if not isinstance(payload, dict):
        raise ModelOutputValidationError(
            [ModelOutputIssue(code="unparseable_output", path="$")]
        )
    try:
        model = _PlanDraft.model_validate(payload)
    except ValidationError as exc:
        issues = []
        for item in exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        ):
            issues.append(
                ModelOutputIssue(
                    code=_schema_issue_code(str(item.get("type") or "unknown")),
                    path=_path(item.get("loc") or ()),
                )
            )
        raise ModelOutputValidationError(issues) from None

    issues: list[ModelOutputIssue] = []
    if model.persona != expected_persona:
        issues.append(ModelOutputIssue("persona_mismatch", "$.persona"))
    if model.area_anchor != expected_area_anchor:
        issues.append(ModelOutputIssue("area_anchor_mismatch", "$.area_anchor"))

    expected_indices = list(range(1, len(model.steps) + 1))
    if [step.step_index for step in model.steps] != expected_indices:
        issues.append(ModelOutputIssue("step_index_sequence_invalid", "$.steps"))

    depart_indices = [index for index, step in enumerate(model.steps) if step.kind == "depart"]
    if len(depart_indices) != 1:
        issues.append(ModelOutputIssue("depart_count_invalid", "$.steps"))
    elif depart_indices[0] != len(model.steps) - 1:
        issues.append(ModelOutputIssue("depart_not_last", f"$.steps[{depart_indices[0]}]"))

    seen: set[str] = set()
    previous_end: int | None = None
    for index, step in enumerate(model.steps):
        path = f"$.steps[{index}]"
        start = _minutes(step.start_time)
        if previous_end is not None and start < previous_end:
            issues.append(ModelOutputIssue("step_time_overlap", f"{path}.start_time"))
        previous_end = start + step.duration_min

        if step.kind == "depart":
            if step.poi_id is not None:
                issues.append(ModelOutputIssue("depart_has_poi_id", f"{path}.poi_id"))
            if step.duration_min != 0:
                issues.append(ModelOutputIssue("depart_duration_invalid", f"{path}.duration_min"))
            continue
        if step.poi_id is None:
            issues.append(ModelOutputIssue("non_depart_missing_poi_id", f"{path}.poi_id"))
            continue
        if step.poi_id in seen:
            issues.append(ModelOutputIssue("duplicate_poi_id", f"{path}.poi_id"))
        seen.add(step.poi_id)
        expected_name = candidate_names_by_id.get(step.poi_id)
        if expected_name is None:
            issues.append(ModelOutputIssue("candidate_id_not_allowed", f"{path}.poi_id"))
        elif step.poi_name != expected_name:
            issues.append(ModelOutputIssue("candidate_name_mismatch", f"{path}.poi_name"))
        if (
            expected_name is not None
            and step.kind in _FOOD_ONLY_STEP_KINDS
            and "food" not in candidate_categories_by_id.get(step.poi_id, ())
        ):
            issues.append(ModelOutputIssue("candidate_category_mismatch", f"{path}.kind"))

    if issues:
        raise ModelOutputValidationError(issues)
    return model.model_dump(mode="python")


def _schema_issue_code(error_type: str) -> str:
    if error_type == "missing":
        return "schema_missing_field"
    if error_type == "extra_forbidden":
        return "schema_extra_field"
    if error_type in {
        "string_type",
        "int_type",
        "list_type",
        "dict_type",
        "model_type",
        "none_required",
    }:
        return "schema_type_invalid"
    if error_type == "literal_error":
        return "schema_literal_invalid"
    return "schema_value_invalid"


def _path(location: tuple[Any, ...] | list[Any]) -> str:
    path = "$"
    for item in location:
        if isinstance(item, int):
            path += f"[{item}]"
        else:
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(item))[:80]
            path += f".{safe}"
    return path


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)
