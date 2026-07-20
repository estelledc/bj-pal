"""Privacy-minimized, deterministic triage for durable planning jobs.

The classifier reports only failure signatures that are directly supported by
the persisted job state and append-only event history.  In particular,
``planning_execution_failed`` remains an unknown runtime-or-dependency class;
the module never upgrades that sanitized boundary into a fabricated root cause.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal, Mapping, Sequence

from .models import PlanningJob, PlanningJobEvent


JOB_DIAGNOSIS_VERSION = "job_incident_diagnosis_v1"
MAX_DIAGNOSTIC_EVENTS = 1000

JobIncidentClassification = Literal[
    "in_progress",
    "retry_pending",
    "lease_recovery_in_progress",
    "completed",
    "cancelled",
    "queue_deadline_exceeded",
    "execution_deadline_exceeded",
    "persisted_request_invalid",
    "clarification_required",
    "execution_budget_exceeded",
    "model_output_rejected",
    "worker_lease_exhausted",
    "runtime_or_dependency_unknown",
    "unclassified_failure",
]
RecommendedAction = Literal[
    "wait_for_worker",
    "wait_for_scheduled_retry",
    "monitor_reclaimed_worker",
    "none",
    "resubmit_with_clarification",
    "inspect_persisted_request_migration",
    "reduce_work_or_adjust_server_budget",
    "inspect_model_output_contract_cases",
    "inspect_worker_health_before_replay",
    "inspect_dependency_health_before_replay",
    "review_deadline_and_queue_capacity_before_replay",
    "manual_review",
]

_TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "dead_lettered",
    "cancelled",
    "timed_out",
}
_FAILURE_SIGNAL_EVENTS = {
    "retry_scheduled",
    "lease_reclaimed",
    "failed",
    "dead_lettered",
    "timed_out",
}
_SAFE_ERROR_CODES = {
    "invalid_persisted_request",
    "clarification_required",
    "execution_budget_exceeded",
    "invalid_model_output",
    "planning_execution_failed",
    "job_deadline_exceeded",
    "lease_expired_attempts_exhausted",
}
_JOB_STATUSES = {
    "queued",
    "running",
    "succeeded",
    "failed",
    "dead_lettered",
    "cancelled",
    "timed_out",
}
_JOB_EVENT_TYPES = {
    "submitted",
    "claimed",
    "heartbeat",
    "retry_scheduled",
    "lease_reclaimed",
    "cancel_requested",
    "cancelled",
    "replay_requested",
    "timed_out",
    "succeeded",
    "failed",
    "dead_lettered",
}


class JobDiagnosticEventLimitExceeded(RuntimeError):
    """The bounded diagnostic reader refused to truncate a long event chain."""


def _canonical_sha256(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("job diagnostic timestamps must include a timezone")
    return parsed


def _safe_error_code(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return "unclassified_error"
    return value if value in _SAFE_ERROR_CODES else "unclassified_error"


@dataclass(frozen=True)
class DiagnosticEvent:
    event_id: int
    event_type: str
    attempt: int
    offset_ms: float
    error_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JobIncidentDiagnosis:
    version: str
    job_id: str
    status: str
    classification: JobIncidentClassification
    classification_basis: tuple[str, ...]
    observed_error_code: str | None
    recommended_action: RecommendedAction
    replay_allowed: bool
    event_count: int
    significant_events: tuple[DiagnosticEvent, ...]
    first_failure_event_id: int | None
    terminal_event_id: int | None
    retry_count: int
    lease_reclaim_count: int
    heartbeat_count: int
    queue_wait_ms: float | None
    time_to_terminal_ms: float | None
    event_sequence_sha256: str
    artifact_sha256: str

    @classmethod
    def create(
        cls,
        *,
        job: PlanningJob,
        events: Sequence[PlanningJobEvent],
    ) -> "JobIncidentDiagnosis":
        projections = _validate_and_project(job, events)
        event_types = tuple(item["event_type"] for item in projections)
        safe_error = _safe_error_code(job.error_code)
        classification = _classify(
            status=job.status,
            error_code=safe_error,
            event_types=event_types,
        )
        baseline = _timestamp(events[0].created_at)
        significant_events = tuple(
            DiagnosticEvent(
                event_id=event.event_id,
                event_type=event.event_type,
                attempt=event.attempt,
                offset_ms=round(
                    max(0.0, (_timestamp(event.created_at) - baseline).total_seconds())
                    * 1000,
                    3,
                ),
                error_code=_safe_error_code(event.payload.get("error_code")),
            )
            for event in events
            if event.event_type != "heartbeat"
        )
        first_claim = next(
            (item for item in significant_events if item.event_type == "claimed"),
            None,
        )
        terminal = next(
            (
                item
                for item in reversed(significant_events)
                if item.event_type == job.status and job.status in _TERMINAL_STATUSES
            ),
            None,
        )
        first_failure = next(
            (
                item
                for item in significant_events
                if item.event_type in _FAILURE_SIGNAL_EVENTS
            ),
            None,
        )
        payload = {
            "version": JOB_DIAGNOSIS_VERSION,
            "job_id": job.job_id,
            "status": job.status,
            "classification": classification,
            "classification_basis": list(
                _classification_basis(
                    status=job.status,
                    error_code=safe_error,
                    event_types=event_types,
                )
            ),
            "observed_error_code": safe_error,
            "recommended_action": _recommended_action(classification),
            "replay_allowed": job.status in {"failed", "dead_lettered", "timed_out"},
            "event_count": len(events),
            "significant_events": [item.to_dict() for item in significant_events],
            "first_failure_event_id": (
                first_failure.event_id if first_failure is not None else None
            ),
            "terminal_event_id": terminal.event_id if terminal is not None else None,
            "retry_count": event_types.count("retry_scheduled"),
            "lease_reclaim_count": event_types.count("lease_reclaimed"),
            "heartbeat_count": event_types.count("heartbeat"),
            "queue_wait_ms": (
                first_claim.offset_ms if first_claim is not None else None
            ),
            "time_to_terminal_ms": (
                terminal.offset_ms if terminal is not None else None
            ),
            "event_sequence_sha256": _canonical_sha256(projections),
        }
        return cls._from_payload(payload)

    @classmethod
    def _from_payload(cls, payload: Mapping[str, Any]) -> "JobIncidentDiagnosis":
        unsigned = dict(payload)
        unsigned.pop("artifact_sha256", None)
        return cls(
            version=str(unsigned["version"]),
            job_id=str(unsigned["job_id"]),
            status=str(unsigned["status"]),
            classification=unsigned["classification"],
            classification_basis=tuple(unsigned["classification_basis"]),
            observed_error_code=unsigned["observed_error_code"],
            recommended_action=unsigned["recommended_action"],
            replay_allowed=bool(unsigned["replay_allowed"]),
            event_count=int(unsigned["event_count"]),
            significant_events=tuple(
                DiagnosticEvent(**item) for item in unsigned["significant_events"]
            ),
            first_failure_event_id=unsigned["first_failure_event_id"],
            terminal_event_id=unsigned["terminal_event_id"],
            retry_count=int(unsigned["retry_count"]),
            lease_reclaim_count=int(unsigned["lease_reclaim_count"]),
            heartbeat_count=int(unsigned["heartbeat_count"]),
            queue_wait_ms=unsigned["queue_wait_ms"],
            time_to_terminal_ms=unsigned["time_to_terminal_ms"],
            event_sequence_sha256=str(unsigned["event_sequence_sha256"]),
            artifact_sha256=_canonical_sha256(unsigned),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "JobIncidentDiagnosis":
        diagnosis = cls._from_payload(payload)
        if str(payload.get("artifact_sha256")) != diagnosis.artifact_sha256:
            raise ValueError("job incident diagnosis failed integrity verification")
        return diagnosis

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "job_id": self.job_id,
            "status": self.status,
            "classification": self.classification,
            "classification_basis": list(self.classification_basis),
            "observed_error_code": self.observed_error_code,
            "recommended_action": self.recommended_action,
            "replay_allowed": self.replay_allowed,
            "event_count": self.event_count,
            "significant_events": [item.to_dict() for item in self.significant_events],
            "first_failure_event_id": self.first_failure_event_id,
            "terminal_event_id": self.terminal_event_id,
            "retry_count": self.retry_count,
            "lease_reclaim_count": self.lease_reclaim_count,
            "heartbeat_count": self.heartbeat_count,
            "queue_wait_ms": self.queue_wait_ms,
            "time_to_terminal_ms": self.time_to_terminal_ms,
            "event_sequence_sha256": self.event_sequence_sha256,
            "artifact_sha256": self.artifact_sha256,
        }

    def verify_integrity(self) -> bool:
        payload = self.to_dict()
        observed = payload.pop("artifact_sha256")
        return (
            self.version == JOB_DIAGNOSIS_VERSION
            and observed == _canonical_sha256(payload)
        )


def _validate_and_project(
    job: PlanningJob,
    events: Sequence[PlanningJobEvent],
) -> list[dict[str, Any]]:
    if job.status not in _JOB_STATUSES:
        raise ValueError("job diagnosis status is invalid")
    if not events:
        raise ValueError("job diagnosis requires persisted events")
    if len(events) > MAX_DIAGNOSTIC_EVENTS:
        raise ValueError("job diagnosis event limit exceeded")
    if events[0].event_type != "submitted":
        raise ValueError("job diagnosis requires the submitted event")
    previous_id = 0
    previous_time: datetime | None = None
    projections: list[dict[str, Any]] = []
    for event in events:
        created_at = _timestamp(event.created_at)
        if event.event_type not in _JOB_EVENT_TYPES:
            raise ValueError("job diagnosis event type is invalid")
        if event.job_id != job.job_id:
            raise ValueError("job diagnosis events belong to another job")
        if event.event_id <= previous_id:
            raise ValueError("job diagnosis event IDs must be strictly increasing")
        if previous_time is not None and created_at < previous_time:
            raise ValueError("job diagnosis event timestamps must be monotonic")
        if event.attempt < 0 or event.attempt > job.max_attempts:
            raise ValueError("job diagnosis event attempt is invalid")
        projections.append(
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "attempt": event.attempt,
                "created_at": event.created_at,
                "error_code": _safe_error_code(event.payload.get("error_code")),
            }
        )
        previous_id = event.event_id
        previous_time = created_at
    if job.status in _TERMINAL_STATUSES and not any(
        item["event_type"] == job.status for item in projections
    ):
        raise ValueError("terminal job is missing its matching terminal event")
    return projections


def _classify(
    *,
    status: str,
    error_code: str | None,
    event_types: Sequence[str],
) -> JobIncidentClassification:
    if status == "queued":
        return "retry_pending" if "retry_scheduled" in event_types else "in_progress"
    if status == "running":
        return (
            "lease_recovery_in_progress"
            if "lease_reclaimed" in event_types
            else "in_progress"
        )
    if status == "succeeded":
        return "completed"
    if status == "cancelled":
        return "cancelled"
    if status == "timed_out":
        return (
            "execution_deadline_exceeded"
            if "claimed" in event_types or "lease_reclaimed" in event_types
            else "queue_deadline_exceeded"
        )
    if error_code == "invalid_persisted_request":
        return "persisted_request_invalid"
    if error_code == "clarification_required":
        return "clarification_required"
    if error_code == "execution_budget_exceeded":
        return "execution_budget_exceeded"
    if error_code == "invalid_model_output":
        return "model_output_rejected"
    if error_code == "lease_expired_attempts_exhausted":
        return "worker_lease_exhausted"
    if status == "dead_lettered" and "lease_reclaimed" in event_types:
        return "worker_lease_exhausted"
    if error_code == "planning_execution_failed":
        return "runtime_or_dependency_unknown"
    if status in {"failed", "dead_lettered"}:
        return "unclassified_failure"
    return "in_progress"


def _classification_basis(
    *,
    status: str,
    error_code: str | None,
    event_types: Sequence[str],
) -> tuple[str, ...]:
    basis = [f"status:{status}"]
    if error_code is not None:
        basis.append(f"error_code:{error_code}")
    if "retry_scheduled" in event_types:
        basis.append("event:retry_scheduled")
    if "lease_reclaimed" in event_types:
        basis.append("event:lease_reclaimed")
    if status == "timed_out":
        basis.append(
            "phase:execution"
            if "claimed" in event_types or "lease_reclaimed" in event_types
            else "phase:queue"
        )
    return tuple(basis)


def _recommended_action(
    classification: JobIncidentClassification,
) -> RecommendedAction:
    return {
        "in_progress": "wait_for_worker",
        "retry_pending": "wait_for_scheduled_retry",
        "lease_recovery_in_progress": "monitor_reclaimed_worker",
        "completed": "none",
        "cancelled": "none",
        "queue_deadline_exceeded": "review_deadline_and_queue_capacity_before_replay",
        "execution_deadline_exceeded": (
            "review_deadline_and_queue_capacity_before_replay"
        ),
        "persisted_request_invalid": "inspect_persisted_request_migration",
        "clarification_required": "resubmit_with_clarification",
        "execution_budget_exceeded": "reduce_work_or_adjust_server_budget",
        "model_output_rejected": "inspect_model_output_contract_cases",
        "worker_lease_exhausted": "inspect_worker_health_before_replay",
        "runtime_or_dependency_unknown": (
            "inspect_dependency_health_before_replay"
        ),
        "unclassified_failure": "manual_review",
    }[classification]
