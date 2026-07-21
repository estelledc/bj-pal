"""Versioned HTTP schemas kept separate from application dataclasses."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents.model_output_contract import ModelOutputContractSnapshot
from agents.types import Persona, UserPreferences
from application import PlanRequest


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreferencesInput(StrictModel):
    party_size: int = Field(default=3, ge=1, le=20)
    has_child: bool = False
    child_age: Optional[int] = Field(default=None, ge=0, le=17)
    diet_flags: list[str] = Field(default_factory=list, max_length=20)
    walk_radius_km: float = Field(default=1.5, gt=0, le=20)
    budget_per_person: Optional[float] = Field(default=None, ge=0, le=100_000)
    target_start: str = "14:00"
    duration_hours: float = Field(default=4.5, gt=0, le=24)

    @field_validator("diet_flags")
    @classmethod
    def validate_diet_flags(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            item = value.strip()
            if not item or len(item) > 64:
                raise ValueError("diet flags must contain 1-64 non-whitespace characters")
            if item not in normalized:
                normalized.append(item)
        return normalized

    @field_validator("target_start")
    @classmethod
    def validate_target_start(cls, value: str) -> str:
        try:
            time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("target_start must be a valid HH:MM time") from exc
        if len(value) != 5:
            raise ValueError("target_start must use HH:MM format")
        return value


class PlanCreateRequest(StrictModel):
    user_input: str = Field(min_length=1, max_length=2_000)
    persona: Persona = "family"
    preferences: PreferencesInput = Field(default_factory=PreferencesInput)
    area_anchor: str = Field(default="五道营-雍和宫片区", min_length=1, max_length=100)
    user_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    auto_reroute: bool = True

    @field_validator("user_input", "area_anchor")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    def to_application_request(self) -> PlanRequest:
        prefs = self.preferences
        provided_fields = set(self.model_fields_set).intersection(
            {
                "user_input",
                "persona",
                "preferences",
                "area_anchor",
                "user_id",
                "auto_reroute",
            }
        )
        if "preferences" in provided_fields:
            provided_fields.update(
                f"preferences.{field_name}"
                for field_name in prefs.model_fields_set
            )
        return PlanRequest(
            user_input=self.user_input,
            persona=self.persona,
            preferences=UserPreferences(
                persona=self.persona,
                party_size=prefs.party_size,
                has_child=prefs.has_child,
                child_age=prefs.child_age,
                diet_flags=list(prefs.diet_flags),
                walk_radius_km=prefs.walk_radius_km,
                budget_per_person=prefs.budget_per_person,
                target_start=prefs.target_start,
                duration_hours=prefs.duration_hours,
                raw_input=self.user_input,
            ),
            area_anchor=self.area_anchor,
            user_id=self.user_id,
            auto_reroute=self.auto_reroute,
            provided_fields=frozenset(provided_fields),
        )


class PlanningJobSubmitRequest(PlanCreateRequest):
    """Planning input plus durable control policy; sync plans stay policy-free."""

    priority: int = Field(default=0, ge=0, le=9)
    deadline_seconds: int = Field(default=900, ge=1, le=86400)


class PreferencesEcho(StrictModel):
    party_size: int
    has_child: bool
    child_age: Optional[int]
    diet_flags: list[str]
    walk_radius_km: float
    budget_per_person: Optional[float]
    target_start: str
    duration_hours: float


class ClarificationResolutionEcho(StrictModel):
    version: Literal["clarification_resolution_v1"]
    code: str
    field: str
    value: Any
    option_id: str
    answer: str
    decision_sha256: str


class RequestEcho(StrictModel):
    user_input: str
    persona: Persona
    preferences: PreferencesEcho
    area_anchor: str
    user_id: Optional[str]
    auto_reroute: bool
    provided_fields: list[str] = Field(default_factory=list)
    resolutions: list[ClarificationResolutionEcho] = Field(default_factory=list)


class ClarificationContinueRequest(StrictModel):
    option_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    answer: Optional[str] = Field(default=None, min_length=1, max_length=2_000)

    @field_validator("answer")
    @classmethod
    def strip_optional_answer(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("answer must not be blank")
        return normalized


class RequirementSignalResponse(StrictModel):
    code: str
    field: str
    evidence: str
    resolved_value: str


class RequirementAssumptionResponse(StrictModel):
    code: str
    field: str
    value: str
    reason: str


class UnresolvedRequirementResponse(StrictModel):
    code: str
    field: str
    evidence: str
    reason: str


class ClarificationQuestionResponse(StrictModel):
    code: str
    field: str
    prompt: str
    options: list[str] = Field(min_length=2, max_length=3)
    reason: str


class RequirementDecisionResponse(StrictModel):
    version: Literal["requirement_gate_v1"]
    status: Literal[
        "proceed",
        "proceed_with_assumptions",
        "clarification_required",
    ]
    normalized_input: str
    resolved_area_anchor: str
    signals: list[RequirementSignalResponse]
    assumptions: list[RequirementAssumptionResponse]
    unresolved: list[UnresolvedRequirementResponse]
    questions: list[ClarificationQuestionResponse]


class ConstraintEntryResponse(StrictModel):
    field: str
    value: Any
    source: Literal[
        "explicit_structured",
        "user_text",
        "user_clarification",
        "default",
    ]
    evidence: str
    hardness: Literal["hard", "soft"]
    outcome: Literal[
        "applied",
        "matched",
        "kept_explicit",
        "merged",
        "resolved",
        "default",
    ]
    text_value: Any = None


class ConstraintConflictResponse(StrictModel):
    field: str
    structured_value: Any
    text_value: Any
    evidence: str
    reason: str


class ConstraintLedgerResponse(StrictModel):
    version: Literal["constraint_ledger_v1"]
    raw_input: str
    rewritten_query: str
    entries: list[ConstraintEntryResponse]
    conflicts: list[ConstraintConflictResponse]
    warnings: list[str]
    applied_fields: list[str]


class StepResponse(StrictModel):
    step_index: int
    poi_name: str
    start_time: str
    kind: str
    poi_id: Optional[str]
    duration_min: int
    mode_to_here: str
    travel_time_min: int
    travel_distance_m: int
    travel_options: dict[str, Any]
    rationale: str
    is_rerouted: bool
    reroute_reason: str
    risk_tags: list[str]
    booking: Optional[dict[str, Any]]
    confidence: Optional[float]
    confidence_source: str
    confidence_factors: dict[str, Any]
    weather_shelter: str


class DataEvidenceResponse(StrictModel):
    domain: str
    provider: str
    source: str
    classification: str
    provider_reference: str
    freshness: str
    retrieved_at: Optional[str]
    valid_until: Optional[str]
    bookable: bool
    warnings: list[str]


class ProviderIssueResponse(StrictModel):
    domain: str
    code: str
    retryable: bool
    required: bool
    message: str


class ModelOutputContextResponse(StrictModel):
    version: Literal["model_output_contract_v1"]
    status: Literal["accepted", "accepted_after_repair"]
    attempt_count: Literal[1, 2]
    repair_attempted: bool
    candidate_count: int = Field(ge=1)
    issue_codes: list[str]
    artifact_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_attempt_semantics(self) -> "ModelOutputContextResponse":
        snapshot = ModelOutputContractSnapshot.from_dict(self.model_dump())
        if not snapshot.verify_integrity():
            raise ValueError("model output context integrity mismatch")
        return self


class PlanResponse(StrictModel):
    persona: Persona
    area_anchor: str
    steps: list[StepResponse]
    fallback_strategies: dict[str, Any]
    summary: str
    rerouted_at_step: Optional[int]
    plan_id: str
    data_provenance: list[DataEvidenceResponse]
    data_warnings: list[ProviderIssueResponse]
    weather_context: Optional[dict[str, Any]]
    route_context: dict[str, Any] = Field(default_factory=dict)
    schedule_context: dict[str, Any] = Field(default_factory=dict)
    model_output_context: Optional[ModelOutputContextResponse] = None


class RerouteEventResponse(StrictModel):
    failed_step_idx: int
    failed_poi_name: str
    reason: str
    evidence: list[str]
    replacement_poi_name: Optional[str]
    change_magnitude: str
    change_summary_zh: str
    unchanged_steps: list[int]
    notify_strategy: str
    replacement_policy: dict[str, Any] = Field(default_factory=dict)
    route_refresh: dict[str, Any] = Field(default_factory=dict)
    schedule_refresh: dict[str, Any] = Field(default_factory=dict)


class DataProfileResponse(StrictModel):
    name: str
    classification: str
    public_reproducible: bool
    limitations: list[str]


class ExecutionSpanResponse(StrictModel):
    name: str
    span_id: str
    parent_span_id: Optional[str]
    offset_ms: float = Field(ge=0)
    duration_ms: float = Field(ge=0)
    status: Literal["ok", "error"]
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)


class TokenUsageResponse(StrictModel):
    completeness: Literal["complete", "partial", "unavailable", "not_applicable"]
    reported_calls: int = Field(ge=0)
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)


class ExecutionBudgetPolicyResponse(StrictModel):
    max_llm_calls: int = Field(ge=1)
    max_data_provider_batches: int = Field(ge=1)
    max_tool_calls: int = Field(ge=0)
    max_transport_attempts_per_llm_call: int = Field(ge=1)
    max_reported_tokens: int = Field(ge=1)
    max_wall_clock_ms: int = Field(ge=1)


class ExecutionBudgetUsageResponse(StrictModel):
    llm_call_count: int = Field(ge=0)
    data_provider_batch_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    reported_token_call_count: int = Field(ge=0)
    reported_total_tokens: Optional[int] = Field(default=None, ge=0)
    elapsed_ms: float = Field(ge=0)


class ExecutionBudgetSnapshotResponse(StrictModel):
    version: Literal["execution_budget_v1"]
    status: Literal["succeeded", "terminated"]
    termination_reason: Literal[
        "completed",
        "llm_call_limit",
        "data_provider_batch_limit",
        "tool_call_limit",
        "reported_token_limit",
        "wall_clock_limit",
    ]
    policy: ExecutionBudgetPolicyResponse
    usage: ExecutionBudgetUsageResponse
    artifact_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ExecutionObservationResponse(StrictModel):
    version: Literal["execution_observation_v1", "execution_observation_v2"]
    status: Literal["succeeded", "failed", "not_observed"]
    execution_id: str
    correlation_id: Optional[str]
    trace_id: str
    started_at: Optional[str]
    duration_ms: float = Field(ge=0)
    spans: list[ExecutionSpanResponse]
    operation_counts: dict[str, int]
    business_counts: dict[str, int]
    token_usage: TokenUsageResponse
    execution_budget: Optional[ExecutionBudgetSnapshotResponse] = None
    artifact_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_budget_version(self) -> "ExecutionObservationResponse":
        if (
            self.version == "execution_observation_v2"
            and self.status != "not_observed"
            and self.execution_budget is None
        ):
            raise ValueError("execution_observation_v2 requires execution_budget")
        if self.version == "execution_observation_v1" and self.execution_budget is not None:
            raise ValueError("execution_observation_v1 predates execution_budget")
        return self


FeedbackReasonCode = Literal[
    "too_expensive",
    "too_far",
    "schedule_unrealistic",
    "unsuitable_poi",
    "route_issue",
    "weather_issue",
    "availability_issue",
    "group_disagreement",
    "other",
]


class FeedbackInvitationResponse(StrictModel):
    version: Literal["feedback_invitation_v1", "feedback_invitation_v2"]
    invitation_id: str = Field(pattern=r"^fbinv-[a-f0-9]{32}$")
    plan_artifact_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    capability: str = Field(min_length=32, max_length=128, pattern=r"^fbcap-[A-Za-z0-9_-]+$")
    feedback_url: str
    expires_at: str
    classification: Literal["self_reported_unverified"]
    invitation_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    trial_id: Optional[str] = Field(default=None, pattern=r"^trial-[a-f0-9]{32}$")
    participant_id: Optional[str] = Field(default=None, pattern=r"^trpart-[a-f0-9]{32}$")
    consent_notice_sha256: Optional[str] = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def validate_trial_binding(self) -> "FeedbackInvitationResponse":
        trial_values = (
            self.trial_id,
            self.participant_id,
            self.consent_notice_sha256,
        )
        if self.version == "feedback_invitation_v2" and any(
            value is None for value in trial_values
        ):
            raise ValueError("trial feedback invitation requires full trial binding")
        if self.version == "feedback_invitation_v1" and any(
            value is not None for value in trial_values
        ):
            raise ValueError("legacy feedback invitation must not contain trial binding")
        return self


class FeedbackSubmitRequest(StrictModel):
    phase: Literal["decision", "outcome"]
    value: Literal[
        "accepted",
        "requested_change",
        "rejected",
        "completed",
        "partially_completed",
        "abandoned",
    ]
    reason_codes: list[FeedbackReasonCode] = Field(default_factory=list, max_length=9)

    @field_validator("reason_codes")
    @classmethod
    def validate_unique_reasons(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("feedback reason codes must be unique")
        return values

    @model_validator(mode="after")
    def validate_phase_value_and_reasons(self) -> "FeedbackSubmitRequest":
        phase_values = {
            "decision": {"accepted", "requested_change", "rejected"},
            "outcome": {"completed", "partially_completed", "abandoned"},
        }
        if self.value not in phase_values[self.phase]:
            raise ValueError("feedback value does not belong to its phase")
        requires_reason = self.value in {
            "requested_change",
            "rejected",
            "partially_completed",
            "abandoned",
        }
        if requires_reason and not self.reason_codes:
            raise ValueError("this feedback value requires at least one reason code")
        if not requires_reason and self.reason_codes:
            raise ValueError("accepted or completed feedback must not include reason codes")
        return self


class FeedbackReportResponse(StrictModel):
    version: Literal["plan_feedback_report_v1", "plan_feedback_report_v2"]
    feedback_id: str = Field(pattern=r"^fb-[a-f0-9]{32}$")
    plan_id: str
    invitation_id: str = Field(pattern=r"^fbinv-[a-f0-9]{32}$")
    plan_artifact_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    phase: Literal["decision", "outcome"]
    value: Literal[
        "accepted",
        "requested_change",
        "rejected",
        "completed",
        "partially_completed",
        "abandoned",
    ]
    reason_codes: list[FeedbackReasonCode]
    classification: Literal["self_reported_unverified"]
    created_at: str
    report_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    trial_id: Optional[str] = Field(default=None, pattern=r"^trial-[a-f0-9]{32}$")
    participant_id: Optional[str] = Field(default=None, pattern=r"^trpart-[a-f0-9]{32}$")
    consent_notice_sha256: Optional[str] = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def validate_trial_binding(self) -> "FeedbackReportResponse":
        trial_values = (
            self.trial_id,
            self.participant_id,
            self.consent_notice_sha256,
        )
        if self.version == "plan_feedback_report_v2" and any(
            value is None for value in trial_values
        ):
            raise ValueError("trial feedback report requires full trial binding")
        if self.version == "plan_feedback_report_v1" and any(
            value is not None for value in trial_values
        ):
            raise ValueError("legacy feedback report must not contain trial binding")
        return self


class FeedbackCollectionResponse(StrictModel):
    plan_id: str
    reports: list[FeedbackReportResponse]


class FeedbackSummaryResponse(StrictModel):
    version: Literal["plan_feedback_summary_v1"]
    classification: Literal["self_reported_unverified"]
    evidence_level: Literal[
        "no_human_feedback",
        "insufficient_human_feedback",
        "aggregate_self_reported",
    ]
    minimum_phase_samples: int = Field(ge=2)
    phase_counts: dict[str, int]
    value_counts: dict[str, int]
    reason_counts: dict[str, int]
    decision_acceptance_rate: Optional[float] = Field(default=None, ge=0, le=1)
    outcome_completion_rate: Optional[float] = Field(default=None, ge=0, le=1)
    limitations: list[str]


class TrialCreateRequest(StrictModel):
    duration_days: int = Field(default=30, ge=1, le=90)
    retention_days: int = Field(default=90, ge=1, le=365)
    minimum_participants: int = Field(default=5, ge=5, le=100)


class TrialNoticeResponse(StrictModel):
    trial_id: str = Field(pattern=r"^trial-[a-f0-9]{32}$")
    status: Literal["open", "closed"]
    notice: dict[str, Any]
    consent_notice_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    cohort_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class TrialEnrollmentInvitationRequest(StrictModel):
    ttl_seconds: int = Field(default=7 * 24 * 60 * 60, ge=300, le=30 * 24 * 60 * 60)


class TrialEnrollmentInvitationResponse(StrictModel):
    version: Literal["trial_enrollment_invitation_v1"]
    enrollment_invitation_id: str = Field(pattern=r"^trinv-[a-f0-9]{32}$")
    trial_id: str = Field(pattern=r"^trial-[a-f0-9]{32}$")
    capability: str = Field(
        min_length=32,
        max_length=128,
        pattern=r"^trienroll-[A-Za-z0-9_-]+$",
    )
    enroll_url: str
    expires_at: str
    invitation_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class TrialEnrollRequest(StrictModel):
    consent_notice_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    consent_attested: Literal[True]


class TrialParticipantResponse(StrictModel):
    version: Literal["trial_participant_v1"]
    participant_id: str = Field(pattern=r"^trpart-[a-f0-9]{32}$")
    trial_id: str = Field(pattern=r"^trial-[a-f0-9]{32}$")
    capability: str = Field(
        min_length=32,
        max_length=128,
        pattern=r"^tripart-[A-Za-z0-9_-]+$",
    )
    consent_notice_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    consented_at: str
    expires_at: str
    participant_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class TrialParticipantEventResponse(StrictModel):
    version: Literal["trial_participant_event_v1"]
    event_id: str = Field(pattern=r"^trev-[a-f0-9]{32}$")
    trial_id: str = Field(pattern=r"^trial-[a-f0-9]{32}$")
    participant_id: str = Field(pattern=r"^trpart-[a-f0-9]{32}$")
    event_type: Literal["withdrawn"]
    created_at: str
    event_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class TrialEvidenceSummaryResponse(StrictModel):
    version: Literal["trial_evidence_summary_v1"]
    trial_id: str = Field(pattern=r"^trial-[a-f0-9]{32}$")
    cohort_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    status: Literal["open"]
    cutoff_at: str
    classification: Literal["self_reported_unverified"]
    minimum_participants: int = Field(ge=5)
    issued_enrollment_count: int = Field(ge=0)
    enrolled_participant_count: int = Field(ge=0)
    withdrawn_participant_count: int = Field(ge=0)
    eligible_participant_count: int = Field(ge=0)
    included_participant_count: int = Field(ge=0)
    phase_participant_counts: dict[str, int]
    value_counts: dict[str, int]
    reason_counts: dict[str, int]
    decision_acceptance_rate: Optional[float] = Field(default=None, ge=0, le=1)
    outcome_completion_rate: Optional[float] = Field(default=None, ge=0, le=1)
    evidence_level: Literal[
        "no_trial_feedback",
        "insufficient_distinct_participant_capabilities",
        "aggregate_self_reported",
    ]
    evidence_root_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    retention_until: str
    retention_state: Literal["active", "raw_purge_due"]
    limitations: list[str]


class TrialEvidenceSnapshotResponse(StrictModel):
    version: Literal["trial_evidence_snapshot_v1"]
    snapshot_id: str = Field(pattern=r"^trsnap-[a-f0-9]{32}$")
    trial_id: str = Field(pattern=r"^trial-[a-f0-9]{32}$")
    cohort_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    status: Literal["closed"]
    cutoff_at: str
    closed_by: str
    classification: Literal["self_reported_unverified"]
    minimum_participants: int = Field(ge=5)
    issued_enrollment_count: int = Field(ge=0)
    enrolled_participant_count: int = Field(ge=0)
    withdrawn_participant_count: int = Field(ge=0)
    eligible_participant_count: int = Field(ge=0)
    included_participant_count: int = Field(ge=0)
    phase_participant_counts: dict[str, int]
    value_counts: dict[str, int]
    reason_counts: dict[str, int]
    decision_acceptance_rate: Optional[float] = Field(default=None, ge=0, le=1)
    outcome_completion_rate: Optional[float] = Field(default=None, ge=0, le=1)
    evidence_level: Literal[
        "no_trial_feedback",
        "insufficient_distinct_participant_capabilities",
        "aggregate_self_reported",
    ]
    evidence_root_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    retention_until: str
    retention_state: Literal["active", "raw_purge_due"]
    limitations: list[str]
    snapshot_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class PlanCreateResponse(StrictModel):
    request: RequestEcho
    initial_plan: PlanResponse
    final_plan: PlanResponse
    reroute_events: list[RerouteEventResponse]
    data_profile: DataProfileResponse
    requirements: Optional[RequirementDecisionResponse] = None
    constraints: Optional[ConstraintLedgerResponse] = None
    # v5.6 and older persisted job/clarification artifacts predate request-level
    # observation. New executions always include this field; legacy reads stay valid.
    execution: Optional[ExecutionObservationResponse] = None
    # v6.3 delivery capability is intentionally absent from canonical plan artifacts.
    feedback: Optional[FeedbackInvitationResponse] = None


PlanningJobStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "dead_lettered",
    "cancelled",
    "timed_out",
]


class PlanningJobResponse(StrictModel):
    job_id: str
    request_id: str
    tenant_id: str
    submitted_by: str
    status: PlanningJobStatus
    attempt: int
    max_attempts: int
    priority: int = Field(ge=0, le=9)
    deadline_seconds: int
    deadline_at: Optional[str]
    available_at: str
    lease_expires_at: Optional[str]
    created_at: str
    updated_at: str
    cancel_requested_at: Optional[str]
    cancelled_at: Optional[str]
    cancel_reason_code: Optional[str]
    replayed_from_job_id: Optional[str]
    artifact_id: Optional[str]
    artifact_sha256: Optional[str]
    result: Optional[PlanCreateResponse]
    error_code: Optional[str]
    error_message: Optional[str]
    links: dict[str, str]


class PlanningJobEventResponse(StrictModel):
    event_id: int
    job_id: str
    event_type: Literal[
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
    ]
    attempt: int
    worker_id: Optional[str]
    payload: dict[str, Any]
    created_at: str


class PlanningJobEventsResponse(StrictModel):
    job_id: str
    events: list[PlanningJobEventResponse]
    next_after_event_id: int
    links: dict[str, str]


class JobDiagnosticEventResponse(StrictModel):
    event_id: int
    event_type: str
    attempt: int
    offset_ms: float = Field(ge=0)
    error_code: Optional[str]


class JobIncidentDiagnosisResponse(StrictModel):
    version: Literal["job_incident_diagnosis_v1"]
    job_id: str
    status: PlanningJobStatus
    classification: Literal[
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
    classification_basis: list[str]
    observed_error_code: Optional[str]
    recommended_action: Literal[
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
    replay_allowed: bool
    event_count: int = Field(ge=1)
    significant_events: list[JobDiagnosticEventResponse]
    first_failure_event_id: Optional[int]
    terminal_event_id: Optional[int]
    retry_count: int = Field(ge=0)
    lease_reclaim_count: int = Field(ge=0)
    heartbeat_count: int = Field(ge=0)
    queue_wait_ms: Optional[float] = Field(default=None, ge=0)
    time_to_terminal_ms: Optional[float] = Field(default=None, ge=0)
    event_sequence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    links: dict[str, str]


class WorkloadLatencyDistributionResponse(StrictModel):
    sample_count: int = Field(ge=0)
    minimum_ms: Optional[float] = Field(default=None, ge=0)
    p50_ms: Optional[float] = Field(default=None, ge=0)
    p95_ms: Optional[float] = Field(default=None, ge=0)
    p99_ms: Optional[float] = Field(default=None, ge=0)
    maximum_ms: Optional[float] = Field(default=None, ge=0)
    quantile_method: Literal["nearest_rank"]


class WorkloadStatusCountsResponse(StrictModel):
    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    dead_lettered: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    timed_out: int = Field(ge=0)


class DurableWorkloadHealthResponse(StrictModel):
    version: Literal["durable_workload_health_v1"]
    window_start: str
    window_end: str
    window_duration_seconds: int = Field(ge=1, le=2_678_400)
    job_count: int = Field(ge=0, le=1_000)
    terminal_job_count: int = Field(ge=0)
    active_job_count: int = Field(ge=0)
    status_counts: WorkloadStatusCountsResponse
    event_count: int = Field(ge=0, le=10_000)
    retry_job_count: int = Field(ge=0)
    lease_recovery_job_count: int = Field(ge=0)
    terminal_success_rate: Optional[float] = Field(default=None, ge=0, le=1)
    terminal_failure_rate: Optional[float] = Field(default=None, ge=0, le=1)
    dead_letter_rate: Optional[float] = Field(default=None, ge=0, le=1)
    timeout_rate: Optional[float] = Field(default=None, ge=0, le=1)
    cancellation_rate: Optional[float] = Field(default=None, ge=0, le=1)
    retry_job_rate: Optional[float] = Field(default=None, ge=0, le=1)
    lease_recovery_job_rate: Optional[float] = Field(default=None, ge=0, le=1)
    queue_wait_ms: WorkloadLatencyDistributionResponse
    run_duration_ms: WorkloadLatencyDistributionResponse
    time_to_terminal_ms: WorkloadLatencyDistributionResponse
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    links: dict[str, str]


class PlanningAdmissionEventResponse(StrictModel):
    event_id: int
    policy_version: Literal["tenant_admission_v1"]
    tenant_id: str
    submitted_by: str
    request_id: str
    operation: Literal["submit", "replay"]
    decision: Literal["admitted", "rejected", "idempotent_reuse"]
    reason_code: Optional[str]
    job_id: Optional[str]
    idempotency_key_present: bool
    active_jobs_before: int
    recent_submissions_before: int
    active_job_limit: Optional[int]
    submission_limit_per_minute: Optional[int]
    submission_window_seconds: int
    retry_after_seconds: Optional[int]
    created_at: str


class PlanningAdmissionEventsResponse(StrictModel):
    events: list[PlanningAdmissionEventResponse]
    next_after_event_id: int
    links: dict[str, str]


class PlanningJobCancelRequest(StrictModel):
    reason_code: Literal["user_requested", "superseded", "operator_requested"] = (
        "user_requested"
    )


class PlanningJobListItemResponse(StrictModel):
    job_id: str
    request_id: str
    tenant_id: str
    submitted_by: str
    status: PlanningJobStatus
    attempt: int
    max_attempts: int
    priority: int = Field(ge=0, le=9)
    deadline_seconds: int
    deadline_at: Optional[str]
    available_at: str
    created_at: str
    updated_at: str
    cancel_requested_at: Optional[str]
    cancelled_at: Optional[str]
    cancel_reason_code: Optional[str]
    replayed_from_job_id: Optional[str]
    artifact_id: Optional[str]
    error_code: Optional[str]
    links: dict[str, str]


class PlanningJobListResponse(StrictModel):
    jobs: list[PlanningJobListItemResponse]
    next_after_job_id: Optional[str]
    links: dict[str, str]


class OperationQuoteInput(StrictModel):
    provider: Literal["bj-pal-sandbox"]
    reference: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    valid_until: str
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    amount_minor: int = Field(ge=0, le=100_000_000)
    terms_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    sandbox: Literal[True]

    @field_validator("valid_until")
    @classmethod
    def validate_valid_until(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("valid_until must include a timezone")
        return value


class RestaurantBookingActionInput(StrictModel):
    poi_id: str = Field(min_length=1, max_length=128)
    poi_name: str = Field(min_length=1, max_length=200)
    target_time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    party_size: int = Field(ge=1, le=20)
    contact_reference: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


class SideEffectOperationRequest(StrictModel):
    operation_kind: Literal["restaurant_booking"]
    action: RestaurantBookingActionInput
    quote: OperationQuoteInput
    approval_ttl_seconds: int = Field(default=300, ge=1, le=1800)


class OperationApprovalRequest(StrictModel):
    expected_approval_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class OperationDenialRequest(OperationApprovalRequest):
    reason_code: Literal[
        "user_declined",
        "quote_changed",
        "policy_rejected",
        "duplicate_request",
    ]


SideEffectOperationStatus = Literal[
    "pending_approval",
    "approved",
    "denied",
    "expired",
    "executing",
    "succeeded",
    "failed",
    "uncertain",
]


class OperationQuoteResponse(StrictModel):
    provider: str
    reference: str
    valid_until: str
    currency: str
    amount_minor: int
    terms_sha256: str
    sandbox: bool


class SideEffectOperationResponse(StrictModel):
    operation_id: str
    request_id: str
    tenant_id: str
    requested_by: str
    operation_kind: Literal["restaurant_booking"]
    status: SideEffectOperationStatus
    action: dict[str, Any]
    request_sha256: str
    quote: OperationQuoteResponse
    approval_sha256: str
    approval_expires_at: str
    approved_by: Optional[str]
    approved_at: Optional[str]
    denied_by: Optional[str]
    denied_at: Optional[str]
    denial_reason_code: Optional[str]
    attempt: int
    provider_operation_id: Optional[str]
    receipt: Optional[dict[str, Any]]
    receipt_sha256: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    created_at: str
    updated_at: str
    links: dict[str, str]


class OperationEventResponse(StrictModel):
    event_id: int
    operation_id: str
    event_type: Literal[
        "requested",
        "request_reused",
        "approved",
        "denied",
        "expired",
        "execution_started",
        "execution_succeeded",
        "execution_failed",
        "execution_uncertain",
    ]
    actor_id: str
    payload: dict[str, Any]
    created_at: str


class OperationEventsResponse(StrictModel):
    operation_id: str
    events: list[OperationEventResponse]
    next_after_event_id: int
    links: dict[str, str]


class OperationReconciliationResponse(StrictModel):
    reconciliation_id: int
    operation_id: str
    tenant_id: str
    actor_id: str
    outcome: Literal["confirmed", "rejected", "still_unknown", "not_found"]
    provider_operation_id: str
    evidence: dict[str, Any]
    evidence_sha256: str
    receipt_sha256: Optional[str]
    created_at: str


class OperationReconciliationsResponse(StrictModel):
    operation_id: str
    reconciliations: list[OperationReconciliationResponse]
    next_after_reconciliation_id: int
    links: dict[str, str]


class HealthResponse(StrictModel):
    status: Literal["ok"]
    service: Literal["bj-pal"]
    version: str


class TraceExportStatusResponse(StrictModel):
    version: Literal["trace_export_status_v1"]
    backend: Literal["off", "jsonl", "otlp", "invalid"]
    state: Literal["disabled", "configured_unproven", "healthy", "degraded"]
    processor: Literal["none", "sync", "batch"]
    privacy_policy: Literal["trace_export_minimal_v1"]
    semconv_profile: Literal["gen_ai_minimal_v1"]
    content_capture_enabled: Literal[False]
    endpoint_origin_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    export_attempt_count: int = Field(ge=0)
    exported_span_count: int = Field(ge=0)
    failed_span_count: int = Field(ge=0)
    dropped_attribute_count: int = Field(ge=0)
    last_error_code: Optional[str] = Field(default=None, min_length=1, max_length=64)


class OperationalAlertPolicyResponse(StrictModel):
    version: Literal["portfolio_operational_alert_policy_v1"]
    minimum_terminal_jobs: int = Field(ge=1)
    terminal_failure_rate_threshold: float = Field(ge=0, le=1)
    minimum_queue_wait_samples: int = Field(ge=1)
    queue_wait_p95_ms_threshold: float = Field(ge=0)
    minimum_jobs: int = Field(ge=1)
    retry_job_rate_threshold: float = Field(ge=0, le=1)
    trace_backend: Literal["otlp"]


class OperationalAlertRuleResponse(StrictModel):
    rule_id: Literal[
        "terminal_failure_rate",
        "queue_wait_p95_ms",
        "retry_job_rate",
        "trace_export_health",
    ]
    signal: Literal[
        "durable_job_terminal_failure_rate",
        "durable_job_queue_wait_p95_ms",
        "durable_job_retry_rate",
        "otlp_trace_export_state",
    ]
    state: Literal["firing", "healthy", "insufficient_data", "disabled"]
    severity: Literal["warning", "critical"]
    observed_value: Optional[float | str]
    threshold_value: Optional[float | str]
    comparison: Literal["gte", "state"]
    sample_count: int = Field(ge=0)
    required_sample_count: int = Field(ge=1)
    reason_code: Literal[
        "minimum_sample_not_met",
        "threshold_breached",
        "within_threshold",
        "otlp_export_not_configured",
        "export_attempt_not_observed",
        "trace_export_degraded",
        "trace_export_invalid_configuration",
        "trace_export_healthy",
    ]


class OperationalAlertSnapshotResponse(StrictModel):
    version: Literal["operational_alert_snapshot_v1"]
    observed_at: str
    window_start: str
    window_end: str
    policy: OperationalAlertPolicyResponse
    overall_state: Literal["firing", "healthy", "insufficient_data", "disabled"]
    rules: list[OperationalAlertRuleResponse] = Field(min_length=4, max_length=4)
    firing_rule_count: int = Field(ge=0, le=4)
    evaluated_rule_count: int = Field(ge=0, le=4)
    insufficient_data_rule_count: int = Field(ge=0, le=4)
    disabled_rule_count: int = Field(ge=0, le=4)
    workload_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    trace_status_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    links: dict[str, str]


class ReadinessResponse(StrictModel):
    status: Literal["ready", "not_ready"]
    data_profile: str
    classification: str
    checks: dict[str, str]


class ErrorBody(StrictModel):
    code: str
    message: str
    request_id: str
    details: Optional[dict[str, Any]] = None


class ErrorResponse(StrictModel):
    error: ErrorBody
