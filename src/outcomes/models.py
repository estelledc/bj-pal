"""Privacy-minimized evidence models for plan-level human feedback."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


FeedbackPhase = Literal["decision", "outcome"]
FeedbackValue = Literal[
    "accepted",
    "requested_change",
    "rejected",
    "completed",
    "partially_completed",
    "abandoned",
]


@dataclass(frozen=True)
class FeedbackInvitation:
    invitation_id: str
    plan_id: str
    plan_artifact_sha256: str
    data_profile_name: str
    data_profile_classification: str
    capability: str
    issued_at: str
    expires_at: str
    classification: str
    invitation_sha256: str
    version: str = "feedback_invitation_v1"
    trial_id: str | None = None
    participant_id: str | None = None
    consent_notice_sha256: str | None = None

    def to_public_dict(self, *, feedback_url: str) -> dict:
        return {
            "version": self.version,
            "invitation_id": self.invitation_id,
            "plan_artifact_sha256": self.plan_artifact_sha256,
            "capability": self.capability,
            "feedback_url": feedback_url,
            "expires_at": self.expires_at,
            "classification": self.classification,
            "invitation_sha256": self.invitation_sha256,
            **(
                {
                    "trial_id": self.trial_id,
                    "participant_id": self.participant_id,
                    "consent_notice_sha256": self.consent_notice_sha256,
                }
                if self.trial_id is not None
                else {}
            ),
        }


@dataclass(frozen=True)
class PlanFeedbackReport:
    feedback_id: str
    plan_id: str
    invitation_id: str
    plan_artifact_sha256: str
    phase: FeedbackPhase
    value: FeedbackValue
    reason_codes: tuple[str, ...]
    classification: str
    created_at: str
    report_sha256: str
    version: str = "plan_feedback_report_v1"
    trial_id: str | None = None
    participant_id: str | None = None
    consent_notice_sha256: str | None = None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["reason_codes"] = list(self.reason_codes)
        version = payload.pop("version")
        return {
            "version": version,
            **{key: value for key, value in payload.items() if value is not None},
        }


@dataclass(frozen=True)
class TrialCohort:
    trial_id: str
    tenant_id: str
    protocol_version: str
    purpose: str
    consent_notice: dict[str, Any]
    consent_notice_sha256: str
    minimum_participants: int
    starts_at: str
    ends_at: str
    retention_until: str
    created_by: str
    created_at: str
    cohort_sha256: str
    status: Literal["open", "closed"]

    def to_notice_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "status": self.status,
            "notice": self.consent_notice,
            "consent_notice_sha256": self.consent_notice_sha256,
            "cohort_sha256": self.cohort_sha256,
        }


@dataclass(frozen=True)
class TrialEnrollmentInvitation:
    enrollment_invitation_id: str
    trial_id: str
    capability: str
    issued_by: str
    issued_at: str
    expires_at: str
    invitation_sha256: str

    def to_public_dict(self, *, enroll_url: str) -> dict[str, Any]:
        return {
            "version": "trial_enrollment_invitation_v1",
            "enrollment_invitation_id": self.enrollment_invitation_id,
            "trial_id": self.trial_id,
            "capability": self.capability,
            "enroll_url": enroll_url,
            "expires_at": self.expires_at,
            "invitation_sha256": self.invitation_sha256,
        }


@dataclass(frozen=True)
class TrialParticipant:
    participant_id: str
    trial_id: str
    enrollment_invitation_id: str
    capability: str
    consent_notice_sha256: str
    consented_at: str
    expires_at: str
    participant_sha256: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "version": "trial_participant_v1",
            "participant_id": self.participant_id,
            "trial_id": self.trial_id,
            "capability": self.capability,
            "consent_notice_sha256": self.consent_notice_sha256,
            "consented_at": self.consented_at,
            "expires_at": self.expires_at,
            "participant_sha256": self.participant_sha256,
        }


@dataclass(frozen=True)
class TrialParticipation:
    trial_id: str
    participant_id: str
    consent_notice_sha256: str
    participant_sha256: str


@dataclass(frozen=True)
class TrialParticipantEvent:
    event_id: str
    trial_id: str
    participant_id: str
    event_type: Literal["withdrawn"]
    created_at: str
    event_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "trial_participant_event_v1",
            "event_id": self.event_id,
            "trial_id": self.trial_id,
            "participant_id": self.participant_id,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "event_sha256": self.event_sha256,
        }


@dataclass(frozen=True)
class TrialEvidenceSnapshot:
    payload: dict[str, Any]
    snapshot_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload, "snapshot_sha256": self.snapshot_sha256}


@dataclass(frozen=True)
class TrialPurgeReceipt:
    payload: dict[str, Any]
    receipt_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload, "receipt_sha256": self.receipt_sha256}
