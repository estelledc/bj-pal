"""Capability-bound, append-only SQLite repository for human plan feedback."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import (
    FeedbackInvitation,
    FeedbackPhase,
    FeedbackValue,
    PlanFeedbackReport,
    TrialCohort,
    TrialEnrollmentInvitation,
    TrialEvidenceSnapshot,
    TrialParticipant,
    TrialParticipantEvent,
    TrialParticipation,
    TrialPurgeReceipt,
)


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FEEDBACK_DB = ROOT / "runtime" / "plan_feedback.db"
FEEDBACK_CLASSIFICATION = "self_reported_unverified"
FEEDBACK_INVITATION_VERSION = "feedback_invitation_v1"
FEEDBACK_REPORT_VERSION = "plan_feedback_report_v1"
TRIAL_FEEDBACK_INVITATION_VERSION = "feedback_invitation_v2"
TRIAL_FEEDBACK_REPORT_VERSION = "plan_feedback_report_v2"
TRIAL_PROTOCOL_VERSION = "bj_pal_trial_protocol_v1"
TRIAL_PURPOSE = "portfolio_product_evaluation"
TRIAL_MINIMUM_PARTICIPANTS = 5
TRIAL_BACKUP_DISPOSITIONS = frozenset(
    {"no_managed_backups", "operator_attested_backups_purged"}
)
MINIMUM_PHASE_SAMPLES = 5
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
DECISION_VALUES = frozenset({"accepted", "requested_change", "rejected"})
OUTCOME_VALUES = frozenset({"completed", "partially_completed", "abandoned"})
REASON_CODES = frozenset(
    {
        "too_expensive",
        "too_far",
        "schedule_unrealistic",
        "unsuitable_poi",
        "route_issue",
        "weather_issue",
        "availability_issue",
        "group_disagreement",
        "other",
    }
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_feedback_invitations (
    invitation_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    plan_artifact_sha256 TEXT NOT NULL,
    data_profile_name TEXT NOT NULL,
    data_profile_classification TEXT NOT NULL,
    capability_sha256 TEXT NOT NULL UNIQUE,
    classification TEXT NOT NULL CHECK(classification = 'self_reported_unverified'),
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    invitation_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_feedback_invitation_plan
ON plan_feedback_invitations(plan_id, expires_at);

CREATE TRIGGER IF NOT EXISTS plan_feedback_invitations_no_update
BEFORE UPDATE ON plan_feedback_invitations
BEGIN
    SELECT RAISE(ABORT, 'plan feedback invitations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS plan_feedback_invitations_no_delete
BEFORE DELETE ON plan_feedback_invitations
BEGIN
    SELECT RAISE(ABORT, 'plan feedback invitations are append-only');
END;

CREATE TABLE IF NOT EXISTS plan_feedback_reports (
    feedback_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    invitation_id TEXT NOT NULL,
    plan_artifact_sha256 TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('decision', 'outcome')),
    value TEXT NOT NULL CHECK(
        value IN (
            'accepted', 'requested_change', 'rejected',
            'completed', 'partially_completed', 'abandoned'
        )
    ),
    reason_codes_json TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification = 'self_reported_unverified'),
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    FOREIGN KEY(invitation_id) REFERENCES plan_feedback_invitations(invitation_id),
    UNIQUE(plan_id, plan_artifact_sha256, phase),
    UNIQUE(invitation_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_plan_feedback_report_created
ON plan_feedback_reports(phase, created_at, feedback_id);

CREATE TRIGGER IF NOT EXISTS plan_feedback_reports_no_update
BEFORE UPDATE ON plan_feedback_reports
BEGIN
    SELECT RAISE(ABORT, 'plan feedback reports are append-only');
END;
CREATE TRIGGER IF NOT EXISTS plan_feedback_reports_no_delete
BEFORE DELETE ON plan_feedback_reports
BEGIN
    SELECT RAISE(ABORT, 'plan feedback reports are append-only');
END;
"""


TRIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS trial_cohorts (
    trial_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    protocol_version TEXT NOT NULL CHECK(protocol_version = 'bj_pal_trial_protocol_v1'),
    purpose TEXT NOT NULL CHECK(purpose = 'portfolio_product_evaluation'),
    consent_notice_json TEXT NOT NULL,
    consent_notice_sha256 TEXT NOT NULL,
    minimum_participants INTEGER NOT NULL CHECK(minimum_participants >= 5),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    retention_until TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cohort_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trial_enrollment_invitations (
    enrollment_invitation_id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL,
    capability_sha256 TEXT NOT NULL UNIQUE,
    issued_by TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    invitation_sha256 TEXT NOT NULL,
    FOREIGN KEY(trial_id) REFERENCES trial_cohorts(trial_id)
);
CREATE INDEX IF NOT EXISTS idx_trial_enrollment_invitation_trial
ON trial_enrollment_invitations(trial_id, expires_at);

CREATE TABLE IF NOT EXISTS trial_participants (
    participant_id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL,
    enrollment_invitation_id TEXT NOT NULL UNIQUE,
    capability_sha256 TEXT NOT NULL UNIQUE,
    consent_notice_sha256 TEXT NOT NULL,
    consented_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    participant_sha256 TEXT NOT NULL,
    FOREIGN KEY(trial_id) REFERENCES trial_cohorts(trial_id),
    FOREIGN KEY(enrollment_invitation_id)
        REFERENCES trial_enrollment_invitations(enrollment_invitation_id)
);
CREATE INDEX IF NOT EXISTS idx_trial_participant_trial
ON trial_participants(trial_id, consented_at);

CREATE TABLE IF NOT EXISTS trial_participant_events (
    event_id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type = 'withdrawn'),
    created_at TEXT NOT NULL,
    event_sha256 TEXT NOT NULL,
    FOREIGN KEY(trial_id) REFERENCES trial_cohorts(trial_id),
    FOREIGN KEY(participant_id) REFERENCES trial_participants(participant_id),
    UNIQUE(participant_id, event_type)
);

CREATE TABLE IF NOT EXISTS trial_evidence_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL UNIQUE,
    cutoff_at TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    FOREIGN KEY(trial_id) REFERENCES trial_cohorts(trial_id)
);

CREATE TABLE IF NOT EXISTS trial_purge_receipts (
    receipt_id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL UNIQUE,
    tenant_sha256 TEXT NOT NULL,
    purged_at TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trial_cohorts_no_update
BEFORE UPDATE ON trial_cohorts
BEGIN
    SELECT RAISE(ABORT, 'trial cohorts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trial_cohorts_no_delete
BEFORE DELETE ON trial_cohorts
BEGIN
    SELECT RAISE(ABORT, 'trial cohorts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trial_enrollment_invitations_no_update
BEFORE UPDATE ON trial_enrollment_invitations
BEGIN
    SELECT RAISE(ABORT, 'trial enrollment invitations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trial_enrollment_invitations_no_delete
BEFORE DELETE ON trial_enrollment_invitations
BEGIN
    SELECT RAISE(ABORT, 'trial enrollment invitations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trial_participants_no_update
BEFORE UPDATE ON trial_participants
BEGIN
    SELECT RAISE(ABORT, 'trial participants are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trial_participants_no_delete
BEFORE DELETE ON trial_participants
BEGIN
    SELECT RAISE(ABORT, 'trial participants are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trial_participant_events_no_update
BEFORE UPDATE ON trial_participant_events
BEGIN
    SELECT RAISE(ABORT, 'trial participant events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trial_participant_events_no_delete
BEFORE DELETE ON trial_participant_events
BEGIN
    SELECT RAISE(ABORT, 'trial participant events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trial_evidence_snapshots_no_update
BEFORE UPDATE ON trial_evidence_snapshots
BEGIN
    SELECT RAISE(ABORT, 'trial evidence snapshots are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trial_evidence_snapshots_no_delete
BEFORE DELETE ON trial_evidence_snapshots
BEGIN
    SELECT RAISE(ABORT, 'trial evidence snapshots are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trial_purge_receipts_no_update
BEFORE UPDATE ON trial_purge_receipts
BEGIN
    SELECT RAISE(ABORT, 'trial purge receipts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trial_purge_receipts_no_delete
BEFORE DELETE ON trial_purge_receipts
BEGIN
    SELECT RAISE(ABORT, 'trial purge receipts are append-only');
END;
"""


RETENTION_PURGE_DELETE_TRIGGERS = {
    "plan_feedback_invitations_no_delete": """
        CREATE TRIGGER plan_feedback_invitations_no_delete
        BEFORE DELETE ON plan_feedback_invitations
        BEGIN
            SELECT RAISE(ABORT, 'plan feedback invitations are append-only');
        END
    """,
    "plan_feedback_reports_no_delete": """
        CREATE TRIGGER plan_feedback_reports_no_delete
        BEFORE DELETE ON plan_feedback_reports
        BEGIN
            SELECT RAISE(ABORT, 'plan feedback reports are append-only');
        END
    """,
    "trial_cohorts_no_delete": """
        CREATE TRIGGER trial_cohorts_no_delete
        BEFORE DELETE ON trial_cohorts
        BEGIN
            SELECT RAISE(ABORT, 'trial cohorts are append-only');
        END
    """,
    "trial_enrollment_invitations_no_delete": """
        CREATE TRIGGER trial_enrollment_invitations_no_delete
        BEFORE DELETE ON trial_enrollment_invitations
        BEGIN
            SELECT RAISE(ABORT, 'trial enrollment invitations are append-only');
        END
    """,
    "trial_participants_no_delete": """
        CREATE TRIGGER trial_participants_no_delete
        BEFORE DELETE ON trial_participants
        BEGIN
            SELECT RAISE(ABORT, 'trial participants are append-only');
        END
    """,
    "trial_participant_events_no_delete": """
        CREATE TRIGGER trial_participant_events_no_delete
        BEFORE DELETE ON trial_participant_events
        BEGIN
            SELECT RAISE(ABORT, 'trial participant events are append-only');
        END
    """,
    "trial_evidence_snapshots_no_delete": """
        CREATE TRIGGER trial_evidence_snapshots_no_delete
        BEFORE DELETE ON trial_evidence_snapshots
        BEGIN
            SELECT RAISE(ABORT, 'trial evidence snapshots are append-only');
        END
    """,
}


class FeedbackNotFound(LookupError):
    """The plan and capability pair does not resolve to an invitation."""


class FeedbackExpired(ValueError):
    """The capability expired before the report was submitted or read."""


class FeedbackIdempotencyConflict(ValueError):
    """An idempotency key was reused for a different report."""


class FeedbackPhaseConflict(ValueError):
    """The plan already has an immutable report for this phase."""


class FeedbackIntegrityError(ValueError):
    """Persisted feedback no longer matches its evidence hash."""


class TrialNotFound(LookupError):
    """The trial or supplied trial capability was not found."""


class TrialNotActive(ValueError):
    """The trial is not inside its collection window."""


class TrialClosed(ValueError):
    """The trial has already been frozen and closed."""


class TrialConsentMismatch(ValueError):
    """The participant did not attest to the exact published notice."""


class TrialEnrollmentConflict(ValueError):
    """A single-use enrollment capability was already consumed."""


class TrialParticipantWithdrawn(ValueError):
    """The participant withdrew and cannot add new evidence."""


class TrialIntegrityError(ValueError):
    """Persisted trial evidence no longer matches its hash chain."""


class TrialPurgeNotReady(ValueError):
    """A frozen evidence snapshot is required before retention purge."""


class TrialRetentionNotDue(ValueError):
    """The trial retention deadline has not elapsed."""


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def capability_sha256(capability: str) -> str:
    return hashlib.sha256(capability.encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("feedback timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_identifier(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not SAFE_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must contain 1-128 safe characters")


def normalize_report(
    *, phase: str, value: str, reason_codes: tuple[str, ...] | list[str]
) -> tuple[FeedbackPhase, FeedbackValue, tuple[str, ...]]:
    if phase not in {"decision", "outcome"}:
        raise ValueError("feedback phase must be decision or outcome")
    allowed_values = DECISION_VALUES if phase == "decision" else OUTCOME_VALUES
    if value not in allowed_values:
        raise ValueError("feedback value does not belong to its phase")
    if not isinstance(reason_codes, (tuple, list)):
        raise ValueError("reason_codes must be a list")
    if any(not isinstance(item, str) or item not in REASON_CODES for item in reason_codes):
        raise ValueError("feedback contains an unsupported reason code")
    normalized_reasons = tuple(sorted(set(reason_codes)))
    if len(normalized_reasons) != len(reason_codes):
        raise ValueError("feedback reason codes must be unique")
    needs_reason = value in {
        "requested_change",
        "rejected",
        "partially_completed",
        "abandoned",
    }
    if needs_reason and not normalized_reasons:
        raise ValueError("this feedback value requires at least one reason code")
    if not needs_reason and normalized_reasons:
        raise ValueError("accepted or completed feedback must not include reason codes")
    return phase, value, normalized_reasons  # type: ignore[return-value]


class PlanFeedbackRepository:
    def __init__(self, path: Path | str | None = None) -> None:
        configured = os.environ.get("BJ_PAL_FEEDBACK_DB")
        self.path = Path(path or configured or DEFAULT_FEEDBACK_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._ensure_trial_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _ensure_trial_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(TRIAL_SCHEMA)
        invitation_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(plan_feedback_invitations)"
            ).fetchall()
        }
        report_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(plan_feedback_reports)"
            ).fetchall()
        }
        trial_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(trial_cohorts)").fetchall()
        }
        if "tenant_id" not in trial_columns:
            connection.execute(
                "ALTER TABLE trial_cohorts "
                "ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
        invitation_additions = {
            "evidence_version": (
                "TEXT NOT NULL DEFAULT 'feedback_invitation_v1'"
            ),
            "trial_id": "TEXT",
            "participant_id": "TEXT",
            "consent_notice_sha256": "TEXT",
        }
        report_additions = {
            "evidence_version": "TEXT NOT NULL DEFAULT 'plan_feedback_report_v1'",
            "trial_id": "TEXT",
            "participant_id": "TEXT",
            "consent_notice_sha256": "TEXT",
        }
        for column, definition in invitation_additions.items():
            if column not in invitation_columns:
                connection.execute(
                    f"ALTER TABLE plan_feedback_invitations "
                    f"ADD COLUMN {column} {definition}"
                )
        for column, definition in report_additions.items():
            if column not in report_columns:
                connection.execute(
                    f"ALTER TABLE plan_feedback_reports ADD COLUMN {column} {definition}"
                )
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_plan_feedback_invitation_trial
            ON plan_feedback_invitations(trial_id, participant_id, issued_at);
            CREATE INDEX IF NOT EXISTS idx_plan_feedback_report_trial
            ON plan_feedback_reports(trial_id, participant_id, phase, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_trial_participant_phase
            ON plan_feedback_reports(trial_id, participant_id, phase)
            WHERE trial_id IS NOT NULL;
            """
        )

    def issue(
        self,
        *,
        plan_id: str,
        plan_artifact_sha256: str,
        data_profile_name: str,
        data_profile_classification: str,
        trial_participant_capability: str | None = None,
        ttl_seconds: int = 14 * 24 * 60 * 60,
        now: datetime | None = None,
    ) -> FeedbackInvitation:
        for field, value in (
            ("plan_id", plan_id),
            ("data_profile_name", data_profile_name),
            ("data_profile_classification", data_profile_classification),
        ):
            _validate_identifier(value, field=field)
        if not SHA256_PATTERN.fullmatch(plan_artifact_sha256):
            raise ValueError("plan_artifact_sha256 must be lowercase SHA-256")
        if not 300 <= ttl_seconds <= 30 * 24 * 60 * 60:
            raise ValueError("feedback ttl_seconds must be between 300 and 2592000")
        issued_at_value = (now or utc_now()).astimezone(timezone.utc)
        issued_at = timestamp(issued_at_value)
        invitation_id = f"fbinv-{uuid.uuid4().hex}"
        capability = f"fbcap-{secrets.token_urlsafe(32)}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            participation: TrialParticipation | None = None
            expires_at_value = issued_at_value + timedelta(seconds=ttl_seconds)
            if trial_participant_capability is not None:
                participation = self._authorized_trial_participant(
                    connection,
                    capability=trial_participant_capability,
                    now=issued_at_value,
                    require_open=True,
                )
                cohort = self._trial_row(connection, participation.trial_id)
                expires_at_value = min(
                    expires_at_value,
                    parse_timestamp(str(cohort["ends_at"])),
                )
            expires_at = timestamp(expires_at_value)
            version = (
                TRIAL_FEEDBACK_INVITATION_VERSION
                if participation is not None
                else FEEDBACK_INVITATION_VERSION
            )
            invitation_payload = {
                "version": version,
                "invitation_id": invitation_id,
                "plan_id": plan_id,
                "plan_artifact_sha256": plan_artifact_sha256,
                "data_profile_name": data_profile_name,
                "data_profile_classification": data_profile_classification,
                "classification": FEEDBACK_CLASSIFICATION,
                "issued_at": issued_at,
                "expires_at": expires_at,
                **(
                    {
                        "trial_id": participation.trial_id,
                        "participant_id": participation.participant_id,
                        "consent_notice_sha256": (
                            participation.consent_notice_sha256
                        ),
                    }
                    if participation is not None
                    else {}
                ),
            }
            invitation_sha256 = sha256_json(invitation_payload)
            connection.execute(
                """
                INSERT INTO plan_feedback_invitations (
                    invitation_id, plan_id, plan_artifact_sha256,
                    data_profile_name, data_profile_classification,
                    capability_sha256, classification, issued_at, expires_at,
                    invitation_sha256, evidence_version, trial_id, participant_id,
                    consent_notice_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invitation_id,
                    plan_id,
                    plan_artifact_sha256,
                    data_profile_name,
                    data_profile_classification,
                    capability_sha256(capability),
                    FEEDBACK_CLASSIFICATION,
                    issued_at,
                    expires_at,
                    invitation_sha256,
                    version,
                    participation.trial_id if participation is not None else None,
                    participation.participant_id if participation is not None else None,
                    (
                        participation.consent_notice_sha256
                        if participation is not None
                        else None
                    ),
                ),
            )
        return FeedbackInvitation(
            invitation_id=invitation_id,
            plan_id=plan_id,
            plan_artifact_sha256=plan_artifact_sha256,
            data_profile_name=data_profile_name,
            data_profile_classification=data_profile_classification,
            capability=capability,
            issued_at=issued_at,
            expires_at=expires_at,
            classification=FEEDBACK_CLASSIFICATION,
            invitation_sha256=invitation_sha256,
            version=version,
            trial_id=participation.trial_id if participation is not None else None,
            participant_id=(
                participation.participant_id if participation is not None else None
            ),
            consent_notice_sha256=(
                participation.consent_notice_sha256
                if participation is not None
                else None
            ),
        )

    def submit(
        self,
        *,
        plan_id: str,
        capability: str,
        idempotency_key: str,
        phase: str,
        value: str,
        reason_codes: tuple[str, ...] | list[str] = (),
        now: datetime | None = None,
    ) -> PlanFeedbackReport:
        _validate_identifier(plan_id, field="plan_id")
        _validate_identifier(idempotency_key, field="idempotency_key")
        if not isinstance(capability, str) or not capability.startswith("fbcap-"):
            raise FeedbackNotFound("feedback invitation was not found")
        normalized_phase, normalized_value, normalized_reasons = normalize_report(
            phase=phase,
            value=value,
            reason_codes=reason_codes,
        )
        now_value = (now or utc_now()).astimezone(timezone.utc)
        created_at = timestamp(now_value)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            invitation = self._authorized_invitation(
                connection,
                plan_id=plan_id,
                capability=capability,
                now=now_value,
            )
            if invitation["trial_id"] is not None:
                self._assert_trial_invitation_open(
                    connection,
                    invitation=invitation,
                    now=now_value,
                )
            report_version = (
                TRIAL_FEEDBACK_REPORT_VERSION
                if invitation["trial_id"] is not None
                else FEEDBACK_REPORT_VERSION
            )
            request_payload = {
                "version": report_version,
                "plan_id": plan_id,
                "plan_artifact_sha256": invitation["plan_artifact_sha256"],
                "phase": normalized_phase,
                "value": normalized_value,
                "reason_codes": list(normalized_reasons),
                "classification": FEEDBACK_CLASSIFICATION,
                **(
                    {
                        "trial_id": invitation["trial_id"],
                        "participant_id": invitation["participant_id"],
                        "consent_notice_sha256": invitation[
                            "consent_notice_sha256"
                        ],
                    }
                    if invitation["trial_id"] is not None
                    else {}
                ),
            }
            request_sha256 = sha256_json(request_payload)
            existing_retry = connection.execute(
                """
                SELECT * FROM plan_feedback_reports
                WHERE invitation_id = ? AND idempotency_key = ?
                """,
                (invitation["invitation_id"], idempotency_key),
            ).fetchone()
            if existing_retry is not None:
                if not hmac.compare_digest(
                    str(existing_retry["request_sha256"]), request_sha256
                ):
                    raise FeedbackIdempotencyConflict(
                        "idempotency key belongs to another feedback report"
                    )
                return self._report_from_row(existing_retry)
            if connection.execute(
                """
                SELECT 1 FROM plan_feedback_reports
                WHERE plan_id = ? AND plan_artifact_sha256 = ? AND phase = ?
                """,
                (
                    plan_id,
                    invitation["plan_artifact_sha256"],
                    normalized_phase,
                ),
            ).fetchone() is not None:
                raise FeedbackPhaseConflict(
                    "plan artifact already has a report for this phase"
                )
            if invitation["trial_id"] is not None and connection.execute(
                """
                SELECT 1 FROM plan_feedback_reports
                WHERE trial_id = ? AND participant_id = ? AND phase = ?
                """,
                (
                    invitation["trial_id"],
                    invitation["participant_id"],
                    normalized_phase,
                ),
            ).fetchone() is not None:
                raise FeedbackPhaseConflict(
                    "trial participant already has a report for this phase"
                )
            feedback_id = f"fb-{uuid.uuid4().hex}"
            report_payload = {
                **request_payload,
                "feedback_id": feedback_id,
                "invitation_id": invitation["invitation_id"],
                "created_at": created_at,
            }
            report_sha256 = sha256_json(report_payload)
            connection.execute(
                """
                INSERT INTO plan_feedback_reports (
                    feedback_id, plan_id, invitation_id, plan_artifact_sha256,
                    phase, value, reason_codes_json, classification,
                    idempotency_key, request_sha256, created_at, report_sha256,
                    evidence_version, trial_id, participant_id,
                    consent_notice_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    plan_id,
                    invitation["invitation_id"],
                    invitation["plan_artifact_sha256"],
                    normalized_phase,
                    normalized_value,
                    canonical_json(list(normalized_reasons)),
                    FEEDBACK_CLASSIFICATION,
                    idempotency_key,
                    request_sha256,
                    created_at,
                    report_sha256,
                    report_version,
                    invitation["trial_id"],
                    invitation["participant_id"],
                    invitation["consent_notice_sha256"],
                ),
            )
            row = connection.execute(
                "SELECT * FROM plan_feedback_reports WHERE feedback_id = ?",
                (feedback_id,),
            ).fetchone()
        assert row is not None
        return self._report_from_row(row)

    def list_reports(
        self,
        *,
        plan_id: str,
        capability: str,
        now: datetime | None = None,
    ) -> tuple[PlanFeedbackReport, ...]:
        _validate_identifier(plan_id, field="plan_id")
        if not isinstance(capability, str) or not capability.startswith("fbcap-"):
            raise FeedbackNotFound("feedback invitation was not found")
        with self._connect() as connection:
            invitation = self._authorized_invitation(
                connection,
                plan_id=plan_id,
                capability=capability,
                now=(now or utc_now()).astimezone(timezone.utc),
            )
            rows = connection.execute(
                """
                SELECT * FROM plan_feedback_reports
                WHERE plan_id = ? AND plan_artifact_sha256 = ?
                ORDER BY CASE phase WHEN 'decision' THEN 0 ELSE 1 END, created_at
                """,
                (plan_id, invitation["plan_artifact_sha256"]),
            ).fetchall()
        return tuple(self._report_from_row(row) for row in rows)

    def public_summary(self, *, minimum_phase_samples: int = MINIMUM_PHASE_SAMPLES) -> dict:
        if minimum_phase_samples < 2:
            raise ValueError("minimum_phase_samples must be at least 2")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM plan_feedback_reports WHERE trial_id IS NULL"
            ).fetchall()
        reports = tuple(self._report_from_row(row) for row in rows)
        phase_counts = Counter(report.phase for report in reports)
        value_counts = Counter(report.value for report in reports)
        reason_counts_by_phase: dict[str, Counter[str]] = {
            "decision": Counter(),
            "outcome": Counter(),
        }
        for report in reports:
            reason_counts_by_phase[report.phase].update(report.reason_codes)
        decision_n = phase_counts["decision"]
        outcome_n = phase_counts["outcome"]
        decision_rate = (
            round(value_counts["accepted"] / decision_n, 4)
            if decision_n >= minimum_phase_samples
            else None
        )
        outcome_rate = (
            round(value_counts["completed"] / outcome_n, 4)
            if outcome_n >= minimum_phase_samples
            else None
        )
        if not reports:
            evidence_level = "no_human_feedback"
        elif decision_rate is None and outcome_rate is None:
            evidence_level = "insufficient_human_feedback"
        else:
            evidence_level = "aggregate_self_reported"
        visible_values: dict[str, int] = {}
        visible_reasons: Counter[str] = Counter()
        if decision_n >= minimum_phase_samples:
            visible_values.update(
                {
                    value: value_counts[value]
                    for value in sorted(DECISION_VALUES)
                    if value_counts[value]
                }
            )
            visible_reasons.update(reason_counts_by_phase["decision"])
        if outcome_n >= minimum_phase_samples:
            visible_values.update(
                {
                    value: value_counts[value]
                    for value in sorted(OUTCOME_VALUES)
                    if value_counts[value]
                }
            )
            visible_reasons.update(reason_counts_by_phase["outcome"])
        return {
            "version": "plan_feedback_summary_v1",
            "classification": FEEDBACK_CLASSIFICATION,
            "evidence_level": evidence_level,
            "minimum_phase_samples": minimum_phase_samples,
            "phase_counts": {"decision": decision_n, "outcome": outcome_n},
            "value_counts": visible_values,
            "reason_counts": dict(sorted(visible_reasons.items())),
            "decision_acceptance_rate": decision_rate,
            "outcome_completion_rate": outcome_rate,
            "limitations": [
                "Feedback is self-reported and not independently verified.",
                "Rates are hidden until the relevant phase reaches the minimum sample size.",
                "Decision and outcome reports are plan-level and do not calibrate step confidence.",
                "Trial-bound reports are excluded and must be read from their frozen cohort evidence.",
            ],
        }

    def create_trial(
        self,
        *,
        created_by: str,
        tenant_id: str = "default",
        duration_days: int = 30,
        retention_days: int = 90,
        minimum_participants: int = TRIAL_MINIMUM_PARTICIPANTS,
        now: datetime | None = None,
    ) -> TrialCohort:
        _validate_identifier(created_by, field="created_by")
        _validate_identifier(tenant_id, field="tenant_id")
        if isinstance(duration_days, bool) or not 1 <= duration_days <= 90:
            raise ValueError("duration_days must be between 1 and 90")
        if isinstance(retention_days, bool) or not 1 <= retention_days <= 365:
            raise ValueError("retention_days must be between 1 and 365")
        if (
            isinstance(minimum_participants, bool)
            or not TRIAL_MINIMUM_PARTICIPANTS <= minimum_participants <= 100
        ):
            raise ValueError("minimum_participants must be between 5 and 100")
        created_at_value = (now or utc_now()).astimezone(timezone.utc)
        starts_at = timestamp(created_at_value)
        ends_at_value = created_at_value + timedelta(days=duration_days)
        ends_at = timestamp(ends_at_value)
        retention_until = timestamp(ends_at_value + timedelta(days=retention_days))
        created_at = timestamp(created_at_value)
        trial_id = f"trial-{uuid.uuid4().hex}"
        notice = {
            "version": "trial_consent_notice_v1",
            "trial_id": trial_id,
            "protocol_version": TRIAL_PROTOCOL_VERSION,
            "purpose": TRIAL_PURPOSE,
            "voluntary": True,
            "evidence_classification": FEEDBACK_CLASSIFICATION,
            "data_collected": [
                "random participant and plan identifiers",
                "plan artifact SHA-256 and data-profile classification",
                "enumerated decision/outcome values and reason codes",
                "consent and submission timestamps",
            ],
            "data_not_collected": [
                "name, email, phone number, or account identity",
                "free-text feedback",
                "raw enrollment, participant, or feedback capabilities",
            ],
            "participation_rule": (
                "one operator-issued enrollment capability creates one anonymous "
                "participant capability; each participant contributes at most one "
                "report per phase"
            ),
            "withdrawal_rule": (
                "withdrawal blocks new evidence and excludes the participant from "
                "future cohort aggregates; append-only records remain locally until "
                "the stated retention deadline"
            ),
            "starts_at": starts_at,
            "ends_at": ends_at,
            "retention_until": retention_until,
            "minimum_participants": minimum_participants,
            "limitations": [
                "A distinct capability is not proof of a distinct human identity.",
                "Reports are self-reported and are not an independent outcome audit.",
                "After retention, deletion requires the explicit local operator purge; no hosted scheduler runs automatically.",
            ],
        }
        consent_notice_sha256 = sha256_json(notice)
        cohort_payload = {
            "version": "trial_cohort_v1",
            "trial_id": trial_id,
            "tenant_id": tenant_id,
            "protocol_version": TRIAL_PROTOCOL_VERSION,
            "purpose": TRIAL_PURPOSE,
            "consent_notice_sha256": consent_notice_sha256,
            "minimum_participants": minimum_participants,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "retention_until": retention_until,
            "created_by": created_by,
            "created_at": created_at,
        }
        cohort_sha256 = sha256_json(cohort_payload)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trial_cohorts (
                    trial_id, tenant_id, protocol_version, purpose, consent_notice_json,
                    consent_notice_sha256, minimum_participants, starts_at, ends_at,
                    retention_until, created_by, created_at, cohort_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trial_id,
                    tenant_id,
                    TRIAL_PROTOCOL_VERSION,
                    TRIAL_PURPOSE,
                    canonical_json(notice),
                    consent_notice_sha256,
                    minimum_participants,
                    starts_at,
                    ends_at,
                    retention_until,
                    created_by,
                    created_at,
                    cohort_sha256,
                ),
            )
            row = self._trial_row(connection, trial_id)
        return self._trial_from_row(row, status="open")

    def get_trial(self, trial_id: str, *, tenant_id: str | None = None) -> TrialCohort:
        _validate_identifier(trial_id, field="trial_id")
        if tenant_id is not None:
            _validate_identifier(tenant_id, field="tenant_id")
        with self._connect() as connection:
            row = self._trial_row(connection, trial_id, tenant_id=tenant_id)
            status_value = (
                "closed"
                if connection.execute(
                    "SELECT 1 FROM trial_evidence_snapshots WHERE trial_id = ?",
                    (trial_id,),
                ).fetchone()
                else "open"
            )
        return self._trial_from_row(row, status=status_value)

    def issue_trial_enrollment(
        self,
        *,
        trial_id: str,
        issued_by: str,
        tenant_id: str | None = None,
        ttl_seconds: int = 7 * 24 * 60 * 60,
        now: datetime | None = None,
    ) -> TrialEnrollmentInvitation:
        return self.issue_trial_enrollments(
            trial_id=trial_id,
            issued_by=issued_by,
            tenant_id=tenant_id,
            count=1,
            ttl_seconds=ttl_seconds,
            now=now,
        )[0]

    def issue_trial_enrollments(
        self,
        *,
        trial_id: str,
        issued_by: str,
        count: int,
        tenant_id: str | None = None,
        ttl_seconds: int = 7 * 24 * 60 * 60,
        now: datetime | None = None,
    ) -> tuple[TrialEnrollmentInvitation, ...]:
        """Issue a bounded enrollment batch in one SQLite transaction."""
        _validate_identifier(trial_id, field="trial_id")
        _validate_identifier(issued_by, field="issued_by")
        if tenant_id is not None:
            _validate_identifier(tenant_id, field="tenant_id")
        if isinstance(count, bool) or not 1 <= count <= 100:
            raise ValueError("enrollment count must be between 1 and 100")
        if not 300 <= ttl_seconds <= 30 * 24 * 60 * 60:
            raise ValueError("enrollment ttl_seconds must be between 300 and 2592000")
        issued_at_value = (now or utc_now()).astimezone(timezone.utc)
        invitations: list[TrialEnrollmentInvitation] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cohort = self._require_trial_active(
                connection,
                trial_id=trial_id,
                tenant_id=tenant_id,
                now=issued_at_value,
            )
            issued_at = timestamp(issued_at_value)
            expires_at = timestamp(
                min(
                    issued_at_value + timedelta(seconds=ttl_seconds),
                    parse_timestamp(str(cohort["ends_at"])),
                )
            )
            for _ in range(count):
                enrollment_invitation_id = f"trinv-{uuid.uuid4().hex}"
                capability = f"trienroll-{secrets.token_urlsafe(32)}"
                invitation_payload = {
                    "version": "trial_enrollment_invitation_v1",
                    "enrollment_invitation_id": enrollment_invitation_id,
                    "trial_id": trial_id,
                    "issued_by": issued_by,
                    "issued_at": issued_at,
                    "expires_at": expires_at,
                }
                invitation_sha256 = sha256_json(invitation_payload)
                connection.execute(
                    """
                    INSERT INTO trial_enrollment_invitations (
                        enrollment_invitation_id, trial_id, capability_sha256,
                        issued_by, issued_at, expires_at, invitation_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        enrollment_invitation_id,
                        trial_id,
                        capability_sha256(capability),
                        issued_by,
                        issued_at,
                        expires_at,
                        invitation_sha256,
                    ),
                )
                invitations.append(
                    TrialEnrollmentInvitation(
                        enrollment_invitation_id=enrollment_invitation_id,
                        trial_id=trial_id,
                        capability=capability,
                        issued_by=issued_by,
                        issued_at=issued_at,
                        expires_at=expires_at,
                        invitation_sha256=invitation_sha256,
                    )
                )
        return tuple(invitations)

    def enroll_trial(
        self,
        *,
        trial_id: str,
        enrollment_capability: str,
        consent_notice_sha256: str,
        consent_attested: bool,
        now: datetime | None = None,
    ) -> TrialParticipant:
        _validate_identifier(trial_id, field="trial_id")
        if (
            not isinstance(enrollment_capability, str)
            or not enrollment_capability.startswith("trienroll-")
        ):
            raise TrialNotFound("trial enrollment invitation was not found")
        if not consent_attested:
            raise TrialConsentMismatch("explicit consent attestation is required")
        if not SHA256_PATTERN.fullmatch(consent_notice_sha256):
            raise TrialConsentMismatch("consent notice SHA-256 is invalid")
        consented_at_value = (now or utc_now()).astimezone(timezone.utc)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cohort = self._require_trial_active(
                connection,
                trial_id=trial_id,
                now=consented_at_value,
            )
            if not hmac.compare_digest(
                str(cohort["consent_notice_sha256"]), consent_notice_sha256
            ):
                raise TrialConsentMismatch(
                    "consent attestation does not match the published notice"
                )
            invitation = connection.execute(
                """
                SELECT * FROM trial_enrollment_invitations
                WHERE trial_id = ? AND capability_sha256 = ?
                """,
                (trial_id, capability_sha256(enrollment_capability)),
            ).fetchone()
            if invitation is None:
                raise TrialNotFound("trial enrollment invitation was not found")
            self._validate_trial_enrollment_invitation(invitation)
            if parse_timestamp(str(invitation["expires_at"])) <= consented_at_value:
                raise TrialNotActive("trial enrollment invitation has expired")
            if connection.execute(
                """
                SELECT 1 FROM trial_participants
                WHERE enrollment_invitation_id = ?
                """,
                (invitation["enrollment_invitation_id"],),
            ).fetchone() is not None:
                raise TrialEnrollmentConflict(
                    "trial enrollment invitation was already consumed"
                )
            participant_id = f"trpart-{uuid.uuid4().hex}"
            capability = f"tripart-{secrets.token_urlsafe(32)}"
            consented_at = timestamp(consented_at_value)
            expires_at = str(cohort["ends_at"])
            participant_payload = {
                "version": "trial_participant_v1",
                "participant_id": participant_id,
                "trial_id": trial_id,
                "enrollment_invitation_id": invitation[
                    "enrollment_invitation_id"
                ],
                "consent_notice_sha256": consent_notice_sha256,
                "consented_at": consented_at,
                "expires_at": expires_at,
            }
            participant_sha256 = sha256_json(participant_payload)
            connection.execute(
                """
                INSERT INTO trial_participants (
                    participant_id, trial_id, enrollment_invitation_id,
                    capability_sha256, consent_notice_sha256, consented_at,
                    expires_at, participant_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    participant_id,
                    trial_id,
                    invitation["enrollment_invitation_id"],
                    capability_sha256(capability),
                    consent_notice_sha256,
                    consented_at,
                    expires_at,
                    participant_sha256,
                ),
            )
        return TrialParticipant(
            participant_id=participant_id,
            trial_id=trial_id,
            enrollment_invitation_id=str(invitation["enrollment_invitation_id"]),
            capability=capability,
            consent_notice_sha256=consent_notice_sha256,
            consented_at=consented_at,
            expires_at=expires_at,
            participant_sha256=participant_sha256,
        )

    def authorize_trial_participant(
        self,
        *,
        capability: str,
        now: datetime | None = None,
    ) -> TrialParticipation:
        with self._connect() as connection:
            return self._authorized_trial_participant(
                connection,
                capability=capability,
                now=(now or utc_now()).astimezone(timezone.utc),
                require_open=True,
            )

    def withdraw_trial(
        self,
        *,
        trial_id: str,
        participant_capability: str,
        now: datetime | None = None,
    ) -> TrialParticipantEvent:
        _validate_identifier(trial_id, field="trial_id")
        created_at_value = (now or utc_now()).astimezone(timezone.utc)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            participation = self._authorized_trial_participant(
                connection,
                capability=participant_capability,
                now=created_at_value,
                require_open=True,
                allow_withdrawn=True,
            )
            if participation.trial_id != trial_id:
                raise TrialNotFound("trial participant was not found")
            existing = connection.execute(
                """
                SELECT * FROM trial_participant_events
                WHERE participant_id = ? AND event_type = 'withdrawn'
                """,
                (participation.participant_id,),
            ).fetchone()
            if existing is not None:
                return self._participant_event_from_row(existing)
            event_id = f"trev-{uuid.uuid4().hex}"
            created_at = timestamp(created_at_value)
            event_payload = {
                "version": "trial_participant_event_v1",
                "event_id": event_id,
                "trial_id": trial_id,
                "participant_id": participation.participant_id,
                "event_type": "withdrawn",
                "created_at": created_at,
            }
            event_sha256 = sha256_json(event_payload)
            connection.execute(
                """
                INSERT INTO trial_participant_events (
                    event_id, trial_id, participant_id, event_type,
                    created_at, event_sha256
                ) VALUES (?, ?, ?, 'withdrawn', ?, ?)
                """,
                (
                    event_id,
                    trial_id,
                    participation.participant_id,
                    created_at,
                    event_sha256,
                ),
            )
        return TrialParticipantEvent(
            event_id=event_id,
            trial_id=trial_id,
            participant_id=participation.participant_id,
            event_type="withdrawn",
            created_at=created_at,
            event_sha256=event_sha256,
        )

    def trial_summary(
        self,
        *,
        trial_id: str,
        tenant_id: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        _validate_identifier(trial_id, field="trial_id")
        if tenant_id is not None:
            _validate_identifier(tenant_id, field="tenant_id")
        now_value = (now or utc_now()).astimezone(timezone.utc)
        with self._connect() as connection:
            cohort = self._trial_row(connection, trial_id, tenant_id=tenant_id)
            snapshot = connection.execute(
                "SELECT * FROM trial_evidence_snapshots WHERE trial_id = ?",
                (trial_id,),
            ).fetchone()
            if snapshot is not None:
                return self._snapshot_from_row(
                    connection,
                    cohort=cohort,
                    row=snapshot,
                ).to_dict()
            return self._build_trial_summary(
                connection,
                cohort=cohort,
                cutoff_at=now_value,
                status_value="open",
            )

    def close_trial(
        self,
        *,
        trial_id: str,
        closed_by: str,
        tenant_id: str | None = None,
        now: datetime | None = None,
    ) -> TrialEvidenceSnapshot:
        _validate_identifier(trial_id, field="trial_id")
        _validate_identifier(closed_by, field="closed_by")
        if tenant_id is not None:
            _validate_identifier(tenant_id, field="tenant_id")
        cutoff_at_value = (now or utc_now()).astimezone(timezone.utc)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cohort = self._trial_row(connection, trial_id, tenant_id=tenant_id)
            existing = connection.execute(
                "SELECT * FROM trial_evidence_snapshots WHERE trial_id = ?",
                (trial_id,),
            ).fetchone()
            if existing is not None:
                return self._snapshot_from_row(
                    connection,
                    cohort=cohort,
                    row=existing,
                )
            summary = self._build_trial_summary(
                connection,
                cohort=cohort,
                cutoff_at=cutoff_at_value,
                status_value="closed",
            )
            snapshot_id = f"trsnap-{uuid.uuid4().hex}"
            snapshot_payload = {
                **summary,
                "version": "trial_evidence_snapshot_v1",
                "snapshot_id": snapshot_id,
                "closed_by": closed_by,
            }
            snapshot_sha256 = sha256_json(snapshot_payload)
            connection.execute(
                """
                INSERT INTO trial_evidence_snapshots (
                    snapshot_id, trial_id, cutoff_at, snapshot_json,
                    snapshot_sha256
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    trial_id,
                    summary["cutoff_at"],
                    canonical_json(snapshot_payload),
                    snapshot_sha256,
                ),
            )
        return TrialEvidenceSnapshot(
            payload=snapshot_payload,
            snapshot_sha256=snapshot_sha256,
        )

    def get_trial_purge_receipt(
        self,
        trial_id: str,
        *,
        tenant_id: str | None = None,
    ) -> TrialPurgeReceipt:
        _validate_identifier(trial_id, field="trial_id")
        if tenant_id is not None:
            _validate_identifier(tenant_id, field="tenant_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM trial_purge_receipts WHERE trial_id = ?",
                (trial_id,),
            ).fetchone()
        if row is None or (
            tenant_id is not None
            and not hmac.compare_digest(
                str(row["tenant_sha256"]), capability_sha256(tenant_id)
            )
        ):
            raise TrialNotFound("trial purge receipt was not found")
        return self._purge_receipt_from_row(row)

    def purge_trial(
        self,
        *,
        trial_id: str,
        tenant_id: str,
        purged_by: str,
        secret_bundle_disposed: bool,
        backup_disposition: str,
        now: datetime | None = None,
    ) -> TrialPurgeReceipt:
        """Atomically purge one due cohort and retain only a non-sensitive receipt."""
        _validate_identifier(trial_id, field="trial_id")
        _validate_identifier(tenant_id, field="tenant_id")
        _validate_identifier(purged_by, field="purged_by")
        if secret_bundle_disposed is not True:
            raise ValueError("secret_bundle_disposed must be explicitly attested true")
        if backup_disposition not in TRIAL_BACKUP_DISPOSITIONS:
            raise ValueError(
                "backup_disposition must be no_managed_backups or "
                "operator_attested_backups_purged"
            )
        purged_at_value = (now or utc_now()).astimezone(timezone.utc)
        purged_at = timestamp(purged_at_value)

        with self._connect() as connection:
            connection.execute("BEGIN EXCLUSIVE")
            existing = connection.execute(
                "SELECT * FROM trial_purge_receipts WHERE trial_id = ?",
                (trial_id,),
            ).fetchone()
            if existing is not None:
                if not hmac.compare_digest(
                    str(existing["tenant_sha256"]), capability_sha256(tenant_id)
                ):
                    raise TrialNotFound("trial purge receipt was not found")
                return self._purge_receipt_from_row(existing)

            journal_mode = str(
                connection.execute("PRAGMA journal_mode").fetchone()[0]
            ).lower()
            if journal_mode not in {"delete", "truncate"}:
                raise TrialPurgeNotReady(
                    "retention purge requires SQLite journal_mode DELETE or "
                    f"TRUNCATE; current mode is {journal_mode}"
                )
            connection.execute("PRAGMA secure_delete=ON")
            secure_delete_enabled = bool(
                connection.execute("PRAGMA secure_delete").fetchone()[0]
            )
            if not secure_delete_enabled:
                raise TrialPurgeNotReady(
                    "SQLite secure_delete could not be enabled for retention purge"
                )
            cohort = self._trial_row(connection, trial_id, tenant_id=tenant_id)
            snapshot_row = connection.execute(
                "SELECT * FROM trial_evidence_snapshots WHERE trial_id = ?",
                (trial_id,),
            ).fetchone()
            if snapshot_row is None:
                raise TrialPurgeNotReady(
                    "trial must have a frozen evidence snapshot before purge"
                )
            snapshot = self._snapshot_from_row(
                connection,
                cohort=cohort,
                row=snapshot_row,
            )
            retention_until = parse_timestamp(str(cohort["retention_until"]))
            if purged_at_value < retention_until:
                raise TrialRetentionNotDue(
                    "trial retention deadline has not elapsed"
                )

            count_queries = {
                "feedback_reports": (
                    "SELECT count(*) FROM plan_feedback_reports WHERE trial_id = ?"
                ),
                "feedback_invitations": (
                    "SELECT count(*) FROM plan_feedback_invitations WHERE trial_id = ?"
                ),
                "participant_events": (
                    "SELECT count(*) FROM trial_participant_events WHERE trial_id = ?"
                ),
                "participants": (
                    "SELECT count(*) FROM trial_participants WHERE trial_id = ?"
                ),
                "enrollment_invitations": (
                    "SELECT count(*) FROM trial_enrollment_invitations WHERE trial_id = ?"
                ),
                "evidence_snapshots": (
                    "SELECT count(*) FROM trial_evidence_snapshots WHERE trial_id = ?"
                ),
                "cohorts": "SELECT count(*) FROM trial_cohorts WHERE trial_id = ?",
            }
            deleted_counts = {
                name: int(connection.execute(query, (trial_id,)).fetchone()[0])
                for name, query in count_queries.items()
            }
            receipt_payload = {
                "version": "trial_retention_purge_receipt_v1",
                "receipt_id": f"trpurge-{uuid.uuid4().hex}",
                "trial_id": trial_id,
                "tenant_sha256": capability_sha256(tenant_id),
                "cohort_sha256": str(cohort["cohort_sha256"]),
                "snapshot_sha256": snapshot.snapshot_sha256,
                "evidence_root_sha256": snapshot.payload["evidence_root_sha256"],
                "retention_until": str(cohort["retention_until"]),
                "purged_by_sha256": capability_sha256(purged_by),
                "purged_at": purged_at,
                "deleted_counts": deleted_counts,
                "secret_bundle_disposition": "operator_attested_disposed",
                "backup_disposition": backup_disposition,
                "classification": "operator_attested_unverified",
                "sqlite_deletion_controls": {
                    "journal_mode": journal_mode,
                    "secure_delete": True,
                },
                "retained_data": (
                    "random trial/receipt identifiers, tenant/operator identifier "
                    "hashes, evidence hashes, lifecycle timestamps, deleted row "
                    "counts, and operator attestations only"
                ),
                "limitations": [
                    "The receipt records logical removal from live tables with SQLite secure_delete enabled; it is not forensic-erasure proof.",
                    "Secret-bundle and backup disposition are operator attestations, not external proof.",
                    "Unsalted tenant/operator identifier hashes are pseudonymous and may be guessable when source identifiers have low entropy.",
                    "The retained hashes cannot reconstruct deleted capabilities or reports.",
                ],
            }
            receipt_sha256 = sha256_json(receipt_payload)

            for trigger_name in RETENTION_PURGE_DELETE_TRIGGERS:
                connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')

            delete_queries = {
                "feedback_reports": (
                    "DELETE FROM plan_feedback_reports WHERE trial_id = ?"
                ),
                "feedback_invitations": (
                    "DELETE FROM plan_feedback_invitations WHERE trial_id = ?"
                ),
                "participant_events": (
                    "DELETE FROM trial_participant_events WHERE trial_id = ?"
                ),
                "participants": "DELETE FROM trial_participants WHERE trial_id = ?",
                "enrollment_invitations": (
                    "DELETE FROM trial_enrollment_invitations WHERE trial_id = ?"
                ),
                "evidence_snapshots": (
                    "DELETE FROM trial_evidence_snapshots WHERE trial_id = ?"
                ),
                "cohorts": "DELETE FROM trial_cohorts WHERE trial_id = ?",
            }
            for name, query in delete_queries.items():
                deleted = connection.execute(query, (trial_id,)).rowcount
                if deleted != deleted_counts[name]:
                    raise TrialIntegrityError(
                        f"retention purge row-count mismatch for {name}"
                    )

            for trigger_sql in RETENTION_PURGE_DELETE_TRIGGERS.values():
                connection.execute(trigger_sql)
            foreign_key_errors = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if foreign_key_errors:
                raise TrialIntegrityError(
                    "retention purge would leave foreign-key violations"
                )

            connection.execute(
                """
                INSERT INTO trial_purge_receipts (
                    receipt_id, trial_id, tenant_sha256, purged_at,
                    receipt_json, receipt_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_payload["receipt_id"],
                    trial_id,
                    receipt_payload["tenant_sha256"],
                    purged_at,
                    canonical_json(receipt_payload),
                    receipt_sha256,
                ),
            )
        return TrialPurgeReceipt(
            payload=receipt_payload,
            receipt_sha256=receipt_sha256,
        )

    @staticmethod
    def _trial_row(
        connection: sqlite3.Connection,
        trial_id: str,
        *,
        tenant_id: str | None = None,
    ) -> sqlite3.Row:
        if tenant_id is None:
            row = connection.execute(
                "SELECT * FROM trial_cohorts WHERE trial_id = ?",
                (trial_id,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT * FROM trial_cohorts
                WHERE trial_id = ? AND tenant_id = ?
                """,
                (trial_id, tenant_id),
            ).fetchone()
        if row is None:
            raise TrialNotFound("trial was not found")
        return row

    @staticmethod
    def _trial_from_row(row: sqlite3.Row, *, status: str) -> TrialCohort:
        try:
            notice = json.loads(str(row["consent_notice_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise TrialIntegrityError("trial consent notice is invalid") from exc
        if not isinstance(notice, dict) or not hmac.compare_digest(
            str(row["consent_notice_sha256"]), sha256_json(notice)
        ):
            raise TrialIntegrityError("trial consent notice hash mismatch")
        cohort_payload = {
            "version": "trial_cohort_v1",
            "trial_id": row["trial_id"],
            "tenant_id": row["tenant_id"],
            "protocol_version": row["protocol_version"],
            "purpose": row["purpose"],
            "consent_notice_sha256": row["consent_notice_sha256"],
            "minimum_participants": row["minimum_participants"],
            "starts_at": row["starts_at"],
            "ends_at": row["ends_at"],
            "retention_until": row["retention_until"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
        }
        if not hmac.compare_digest(
            str(row["cohort_sha256"]), sha256_json(cohort_payload)
        ):
            raise TrialIntegrityError("trial cohort hash mismatch")
        if status not in {"open", "closed"}:
            raise TrialIntegrityError("trial status is invalid")
        return TrialCohort(
            trial_id=str(row["trial_id"]),
            tenant_id=str(row["tenant_id"]),
            protocol_version=str(row["protocol_version"]),
            purpose=str(row["purpose"]),
            consent_notice=notice,
            consent_notice_sha256=str(row["consent_notice_sha256"]),
            minimum_participants=int(row["minimum_participants"]),
            starts_at=str(row["starts_at"]),
            ends_at=str(row["ends_at"]),
            retention_until=str(row["retention_until"]),
            created_by=str(row["created_by"]),
            created_at=str(row["created_at"]),
            cohort_sha256=str(row["cohort_sha256"]),
            status=status,  # type: ignore[arg-type]
        )

    def _require_trial_active(
        self,
        connection: sqlite3.Connection,
        *,
        trial_id: str,
        tenant_id: str | None = None,
        now: datetime,
    ) -> sqlite3.Row:
        cohort = self._trial_row(connection, trial_id, tenant_id=tenant_id)
        self._trial_from_row(cohort, status="open")
        if connection.execute(
            "SELECT 1 FROM trial_evidence_snapshots WHERE trial_id = ?",
            (trial_id,),
        ).fetchone() is not None:
            raise TrialClosed("trial evidence has already been frozen")
        if not (
            parse_timestamp(str(cohort["starts_at"]))
            <= now
            < parse_timestamp(str(cohort["ends_at"]))
        ):
            raise TrialNotActive("trial is outside its collection window")
        return cohort

    @staticmethod
    def _validate_trial_enrollment_invitation(row: sqlite3.Row) -> None:
        payload = {
            "version": "trial_enrollment_invitation_v1",
            "enrollment_invitation_id": row["enrollment_invitation_id"],
            "trial_id": row["trial_id"],
            "issued_by": row["issued_by"],
            "issued_at": row["issued_at"],
            "expires_at": row["expires_at"],
        }
        if not hmac.compare_digest(
            str(row["invitation_sha256"]), sha256_json(payload)
        ):
            raise TrialIntegrityError("trial enrollment invitation hash mismatch")

    @staticmethod
    def _participation_from_row(row: sqlite3.Row) -> TrialParticipation:
        payload = {
            "version": "trial_participant_v1",
            "participant_id": row["participant_id"],
            "trial_id": row["trial_id"],
            "enrollment_invitation_id": row["enrollment_invitation_id"],
            "consent_notice_sha256": row["consent_notice_sha256"],
            "consented_at": row["consented_at"],
            "expires_at": row["expires_at"],
        }
        if not hmac.compare_digest(
            str(row["participant_sha256"]), sha256_json(payload)
        ):
            raise TrialIntegrityError("trial participant hash mismatch")
        return TrialParticipation(
            trial_id=str(row["trial_id"]),
            participant_id=str(row["participant_id"]),
            consent_notice_sha256=str(row["consent_notice_sha256"]),
            participant_sha256=str(row["participant_sha256"]),
        )

    def _authorized_trial_participant(
        self,
        connection: sqlite3.Connection,
        *,
        capability: str,
        now: datetime,
        require_open: bool,
        allow_withdrawn: bool = False,
    ) -> TrialParticipation:
        if not isinstance(capability, str) or not capability.startswith("tripart-"):
            raise TrialNotFound("trial participant was not found")
        row = connection.execute(
            "SELECT * FROM trial_participants WHERE capability_sha256 = ?",
            (capability_sha256(capability),),
        ).fetchone()
        if row is None:
            raise TrialNotFound("trial participant was not found")
        participation = self._participation_from_row(row)
        if require_open:
            cohort = self._require_trial_active(
                connection,
                trial_id=participation.trial_id,
                now=now,
            )
        else:
            cohort = self._trial_row(connection, participation.trial_id)
            self._trial_from_row(cohort, status="open")
        if parse_timestamp(str(row["expires_at"])) <= now:
            raise TrialNotActive("trial participant capability has expired")
        if not hmac.compare_digest(
            str(cohort["consent_notice_sha256"]),
            participation.consent_notice_sha256,
        ):
            raise TrialIntegrityError("trial participant consent binding mismatch")
        withdrawn = connection.execute(
            """
            SELECT * FROM trial_participant_events
            WHERE participant_id = ? AND event_type = 'withdrawn'
            """,
            (participation.participant_id,),
        ).fetchone()
        if withdrawn is not None:
            self._participant_event_from_row(withdrawn)
            if not allow_withdrawn:
                raise TrialParticipantWithdrawn("trial participant has withdrawn")
        return participation

    def _assert_trial_invitation_open(
        self,
        connection: sqlite3.Connection,
        *,
        invitation: sqlite3.Row,
        now: datetime,
    ) -> None:
        participant = connection.execute(
            """
            SELECT * FROM trial_participants
            WHERE trial_id = ? AND participant_id = ?
            """,
            (invitation["trial_id"], invitation["participant_id"]),
        ).fetchone()
        if participant is None:
            raise TrialIntegrityError("trial feedback participant binding is missing")
        participation = self._participation_from_row(participant)
        self._require_trial_active(
            connection,
            trial_id=participation.trial_id,
            now=now,
        )
        if (
            participation.trial_id != invitation["trial_id"]
            or participation.participant_id != invitation["participant_id"]
            or not hmac.compare_digest(
                participation.consent_notice_sha256,
                str(invitation["consent_notice_sha256"]),
            )
        ):
            raise TrialIntegrityError("trial feedback invitation binding mismatch")
        withdrawn = connection.execute(
            """
            SELECT * FROM trial_participant_events
            WHERE participant_id = ? AND event_type = 'withdrawn'
            """,
            (participation.participant_id,),
        ).fetchone()
        if withdrawn is not None:
            self._participant_event_from_row(withdrawn)
            raise TrialParticipantWithdrawn("trial participant has withdrawn")

    @staticmethod
    def _participant_event_from_row(row: sqlite3.Row) -> TrialParticipantEvent:
        payload = {
            "version": "trial_participant_event_v1",
            "event_id": row["event_id"],
            "trial_id": row["trial_id"],
            "participant_id": row["participant_id"],
            "event_type": row["event_type"],
            "created_at": row["created_at"],
        }
        if not hmac.compare_digest(
            str(row["event_sha256"]), sha256_json(payload)
        ):
            raise TrialIntegrityError("trial participant event hash mismatch")
        return TrialParticipantEvent(
            event_id=str(row["event_id"]),
            trial_id=str(row["trial_id"]),
            participant_id=str(row["participant_id"]),
            event_type="withdrawn",
            created_at=str(row["created_at"]),
            event_sha256=str(row["event_sha256"]),
        )

    def _build_trial_summary(
        self,
        connection: sqlite3.Connection,
        *,
        cohort: sqlite3.Row,
        cutoff_at: datetime,
        status_value: str,
    ) -> dict:
        trial = self._trial_from_row(cohort, status=status_value)
        cutoff = timestamp(cutoff_at)
        invitation_rows = connection.execute(
            """
            SELECT * FROM trial_enrollment_invitations
            WHERE trial_id = ? AND issued_at <= ?
            ORDER BY enrollment_invitation_id
            """,
            (trial.trial_id, cutoff),
        ).fetchall()
        for row in invitation_rows:
            self._validate_trial_enrollment_invitation(row)
        participant_rows = connection.execute(
            """
            SELECT * FROM trial_participants
            WHERE trial_id = ? AND consented_at <= ?
            ORDER BY participant_id
            """,
            (trial.trial_id, cutoff),
        ).fetchall()
        participations = {
            item.participant_id: item
            for item in (self._participation_from_row(row) for row in participant_rows)
        }
        if any(
            not hmac.compare_digest(
                item.consent_notice_sha256,
                trial.consent_notice_sha256,
            )
            for item in participations.values()
        ):
            raise TrialIntegrityError("trial participant consent cohort mismatch")
        withdrawn_rows = connection.execute(
            """
            SELECT * FROM trial_participant_events
            WHERE trial_id = ? AND created_at <= ?
            ORDER BY event_id
            """,
            (trial.trial_id, cutoff),
        ).fetchall()
        withdrawn_events = tuple(
            self._participant_event_from_row(row) for row in withdrawn_rows
        )
        withdrawn_ids = {event.participant_id for event in withdrawn_events}
        if not withdrawn_ids <= set(participations):
            raise TrialIntegrityError("trial withdrawal references an unknown participant")
        report_rows = connection.execute(
            """
            SELECT * FROM plan_feedback_reports
            WHERE trial_id = ? AND created_at <= ?
            ORDER BY participant_id, phase, feedback_id
            """,
            (trial.trial_id, cutoff),
        ).fetchall()
        reports: list[PlanFeedbackReport] = []
        seen_participant_phases: set[tuple[str, str]] = set()
        for row in report_rows:
            report = self._report_from_row(row)
            if report.participant_id not in participations:
                raise TrialIntegrityError("trial report references an unknown participant")
            participation = participations[report.participant_id]
            if (
                report.version != TRIAL_FEEDBACK_REPORT_VERSION
                or report.trial_id != trial.trial_id
                or not hmac.compare_digest(
                    report.consent_notice_sha256 or "",
                    participation.consent_notice_sha256,
                )
            ):
                raise TrialIntegrityError("trial report cohort binding mismatch")
            participant_phase = (report.participant_id, report.phase)
            if participant_phase in seen_participant_phases:
                raise TrialIntegrityError("trial participant phase is not unique")
            seen_participant_phases.add(participant_phase)
            if report.participant_id not in withdrawn_ids:
                reports.append(report)
        phase_counts = Counter(report.phase for report in reports)
        value_counts = Counter(report.value for report in reports)
        reason_counts_by_phase: dict[str, Counter[str]] = {
            "decision": Counter(),
            "outcome": Counter(),
        }
        for report in reports:
            reason_counts_by_phase[report.phase].update(report.reason_codes)
        decision_n = phase_counts["decision"]
        outcome_n = phase_counts["outcome"]
        minimum = trial.minimum_participants
        decision_rate = (
            round(value_counts["accepted"] / decision_n, 4)
            if decision_n >= minimum
            else None
        )
        outcome_rate = (
            round(value_counts["completed"] / outcome_n, 4)
            if outcome_n >= minimum
            else None
        )
        visible_values: dict[str, int] = {}
        visible_reasons: Counter[str] = Counter()
        if decision_n >= minimum:
            visible_values.update(
                {
                    value: value_counts[value]
                    for value in sorted(DECISION_VALUES)
                    if value_counts[value]
                }
            )
            visible_reasons.update(reason_counts_by_phase["decision"])
        if outcome_n >= minimum:
            visible_values.update(
                {
                    value: value_counts[value]
                    for value in sorted(OUTCOME_VALUES)
                    if value_counts[value]
                }
            )
            visible_reasons.update(reason_counts_by_phase["outcome"])
        included_participant_ids = {
            report.participant_id for report in reports if report.participant_id
        }
        if not reports:
            evidence_level = "no_trial_feedback"
        elif decision_rate is None and outcome_rate is None:
            evidence_level = "insufficient_distinct_participant_capabilities"
        else:
            evidence_level = "aggregate_self_reported"
        eligible_participations = [
            item
            for participant_id, item in participations.items()
            if participant_id not in withdrawn_ids
        ]
        evidence_root_payload = {
            "version": "trial_evidence_root_v1",
            "trial_id": trial.trial_id,
            "cohort_sha256": trial.cohort_sha256,
            "cutoff_at": cutoff,
            "enrollment_invitation_sha256s": sorted(
                str(row["invitation_sha256"]) for row in invitation_rows
            ),
            "eligible_participant_sha256s": sorted(
                item.participant_sha256 for item in eligible_participations
            ),
            "withdrawal_event_sha256s": sorted(
                event.event_sha256 for event in withdrawn_events
            ),
            "included_report_sha256s": sorted(
                report.report_sha256 for report in reports
            ),
        }
        return {
            "version": "trial_evidence_summary_v1",
            "trial_id": trial.trial_id,
            "cohort_sha256": trial.cohort_sha256,
            "status": status_value,
            "cutoff_at": cutoff,
            "classification": FEEDBACK_CLASSIFICATION,
            "minimum_participants": minimum,
            "issued_enrollment_count": len(invitation_rows),
            "enrolled_participant_count": len(participations),
            "withdrawn_participant_count": len(withdrawn_ids),
            "eligible_participant_count": len(eligible_participations),
            "included_participant_count": len(included_participant_ids),
            "phase_participant_counts": {
                "decision": decision_n,
                "outcome": outcome_n,
            },
            "value_counts": visible_values,
            "reason_counts": dict(sorted(visible_reasons.items())),
            "decision_acceptance_rate": decision_rate,
            "outcome_completion_rate": outcome_rate,
            "evidence_level": evidence_level,
            "evidence_root_sha256": sha256_json(evidence_root_payload),
            "retention_until": trial.retention_until,
            "retention_state": (
                "raw_purge_due"
                if cutoff_at >= parse_timestamp(trial.retention_until)
                else "active"
            ),
            "limitations": [
                "Distinct participant capabilities do not prove distinct human identities.",
                "Reports are self-reported and not independently verified.",
                "Rates and categorical distributions are hidden per phase until the cohort threshold is met.",
                "Withdrawal excludes future aggregates; due local data requires the explicit operator purge, while secret and backup disposal remain separate attestations.",
            ],
        }

    def _snapshot_from_row(
        self,
        connection: sqlite3.Connection,
        *,
        cohort: sqlite3.Row,
        row: sqlite3.Row,
    ) -> TrialEvidenceSnapshot:
        try:
            payload = json.loads(str(row["snapshot_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise TrialIntegrityError("trial snapshot JSON is invalid") from exc
        if not isinstance(payload, dict) or not hmac.compare_digest(
            str(row["snapshot_sha256"]), sha256_json(payload)
        ):
            raise TrialIntegrityError("trial snapshot hash mismatch")
        if (
            payload.get("version") != "trial_evidence_snapshot_v1"
            or payload.get("snapshot_id") != row["snapshot_id"]
            or payload.get("trial_id") != row["trial_id"]
        ):
            raise TrialIntegrityError("trial snapshot identity mismatch")
        expected = self._build_trial_summary(
            connection,
            cohort=cohort,
            cutoff_at=parse_timestamp(str(row["cutoff_at"])),
            status_value="closed",
        )
        for key, expected_value in expected.items():
            if key == "version":
                continue
            if payload.get(key) != expected_value:
                raise TrialIntegrityError(
                    f"trial snapshot evidence mismatch for {key}"
                )
        return TrialEvidenceSnapshot(
            payload=payload,
            snapshot_sha256=str(row["snapshot_sha256"]),
        )

    @staticmethod
    def _purge_receipt_from_row(row: sqlite3.Row) -> TrialPurgeReceipt:
        try:
            payload = json.loads(str(row["receipt_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise TrialIntegrityError("trial purge receipt JSON is invalid") from exc
        if not isinstance(payload, dict) or not hmac.compare_digest(
            str(row["receipt_sha256"]), sha256_json(payload)
        ):
            raise TrialIntegrityError("trial purge receipt hash mismatch")
        if (
            payload.get("version") != "trial_retention_purge_receipt_v1"
            or payload.get("receipt_id") != row["receipt_id"]
            or payload.get("trial_id") != row["trial_id"]
            or payload.get("tenant_sha256") != row["tenant_sha256"]
            or payload.get("purged_at") != row["purged_at"]
        ):
            raise TrialIntegrityError("trial purge receipt identity mismatch")
        return TrialPurgeReceipt(
            payload=payload,
            receipt_sha256=str(row["receipt_sha256"]),
        )

    @staticmethod
    def _authorized_invitation(
        connection: sqlite3.Connection,
        *,
        plan_id: str,
        capability: str,
        now: datetime,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM plan_feedback_invitations
            WHERE plan_id = ? AND capability_sha256 = ?
            """,
            (plan_id, capability_sha256(capability)),
        ).fetchone()
        if row is None:
            raise FeedbackNotFound("feedback invitation was not found")
        if parse_timestamp(str(row["expires_at"])) <= now:
            raise FeedbackExpired("feedback invitation has expired")
        version = str(row["evidence_version"])
        invitation_payload = {
            "version": version,
            "invitation_id": row["invitation_id"],
            "plan_id": row["plan_id"],
            "plan_artifact_sha256": row["plan_artifact_sha256"],
            "data_profile_name": row["data_profile_name"],
            "data_profile_classification": row["data_profile_classification"],
            "classification": row["classification"],
            "issued_at": row["issued_at"],
            "expires_at": row["expires_at"],
            **(
                {
                    "trial_id": row["trial_id"],
                    "participant_id": row["participant_id"],
                    "consent_notice_sha256": row["consent_notice_sha256"],
                }
                if version == TRIAL_FEEDBACK_INVITATION_VERSION
                else {}
            ),
        }
        if not hmac.compare_digest(
            str(row["invitation_sha256"]), sha256_json(invitation_payload)
        ):
            raise FeedbackIntegrityError("feedback invitation hash mismatch")
        return row

    @staticmethod
    def _report_from_row(row: sqlite3.Row) -> PlanFeedbackReport:
        reasons = tuple(json.loads(str(row["reason_codes_json"])))
        version = str(row["evidence_version"])
        report_payload = {
            "version": version,
            "plan_id": row["plan_id"],
            "plan_artifact_sha256": row["plan_artifact_sha256"],
            "phase": row["phase"],
            "value": row["value"],
            "reason_codes": list(reasons),
            "classification": row["classification"],
            "feedback_id": row["feedback_id"],
            "invitation_id": row["invitation_id"],
            "created_at": row["created_at"],
            **(
                {
                    "trial_id": row["trial_id"],
                    "participant_id": row["participant_id"],
                    "consent_notice_sha256": row["consent_notice_sha256"],
                }
                if version == TRIAL_FEEDBACK_REPORT_VERSION
                else {}
            ),
        }
        if not hmac.compare_digest(
            str(row["report_sha256"]), sha256_json(report_payload)
        ):
            raise FeedbackIntegrityError("feedback report hash mismatch")
        return PlanFeedbackReport(
            feedback_id=str(row["feedback_id"]),
            plan_id=str(row["plan_id"]),
            invitation_id=str(row["invitation_id"]),
            plan_artifact_sha256=str(row["plan_artifact_sha256"]),
            phase=row["phase"],
            value=row["value"],
            reason_codes=reasons,
            classification=str(row["classification"]),
            created_at=str(row["created_at"]),
            report_sha256=str(row["report_sha256"]),
            version=version,
            trial_id=(str(row["trial_id"]) if row["trial_id"] is not None else None),
            participant_id=(
                str(row["participant_id"])
                if row["participant_id"] is not None
                else None
            ),
            consent_notice_sha256=(
                str(row["consent_notice_sha256"])
                if row["consent_notice_sha256"] is not None
                else None
            ),
        )
