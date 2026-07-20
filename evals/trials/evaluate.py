"""Generate raw evidence for consent-bound trial cohorts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from outcomes import (
    FEEDBACK_CLASSIFICATION,
    TRIAL_MINIMUM_PARTICIPANTS,
    FeedbackPhaseConflict,
    PlanFeedbackRepository,
    TrialClosed,
    TrialConsentMismatch,
    TrialEnrollmentConflict,
    TrialNotActive,
    TrialNotFound,
    TrialParticipantWithdrawn,
    TrialPurgeNotReady,
    TrialRetentionNotDue,
)


NOW = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _error_name(callback) -> str | None:
    try:
        callback()
    except TrialConsentMismatch:
        return "trial_consent_mismatch"
    except TrialEnrollmentConflict:
        return "trial_enrollment_conflict"
    except TrialParticipantWithdrawn:
        return "trial_participant_withdrawn"
    except TrialClosed:
        return "trial_closed"
    except TrialNotActive:
        return "trial_not_active"
    except TrialNotFound:
        return "trial_not_found"
    except TrialPurgeNotReady:
        return "trial_purge_not_ready"
    except TrialRetentionNotDue:
        return "trial_retention_not_due"
    except FeedbackPhaseConflict:
        return "feedback_phase_conflict"
    except ValueError:
        return "invalid_request"
    return None


def _trial(repository: PlanFeedbackRepository, *, tenant: str = "alpha", days: int = 30):
    return repository.create_trial(
        created_by=f"{tenant}-operator",
        tenant_id=tenant,
        duration_days=days,
        retention_days=30,
        now=NOW,
    )


def _participant(repository: PlanFeedbackRepository, trial, *, index: int):
    enrollment = repository.issue_trial_enrollment(
        trial_id=trial.trial_id,
        tenant_id=trial.tenant_id,
        issued_by=f"{trial.tenant_id}-operator",
        now=NOW + timedelta(seconds=index * 2),
    )
    participant = repository.enroll_trial(
        trial_id=trial.trial_id,
        enrollment_capability=enrollment.capability,
        consent_notice_sha256=trial.consent_notice_sha256,
        consent_attested=True,
        now=NOW + timedelta(seconds=index * 2 + 1),
    )
    return enrollment, participant


def _plan(repository: PlanFeedbackRepository, participant, *, index: int):
    return repository.issue(
        plan_id=f"trial-eval-plan-{index}",
        plan_artifact_sha256=f"{index + 1:064x}",
        data_profile_name="demo",
        data_profile_classification="synthetic",
        trial_participant_capability=participant.capability,
        now=NOW + timedelta(minutes=1, seconds=index),
    )


def _consent_case(path: Path) -> dict:
    repository = PlanFeedbackRepository(path)
    trial = _trial(repository)
    enrollment = repository.issue_trial_enrollment(
        trial_id=trial.trial_id,
        tenant_id=trial.tenant_id,
        issued_by="alpha-operator",
        now=NOW,
    )
    wrong_consent_error = _error_name(
        lambda: repository.enroll_trial(
            trial_id=trial.trial_id,
            enrollment_capability=enrollment.capability,
            consent_notice_sha256="0" * 64,
            consent_attested=True,
            now=NOW,
        )
    )
    participant = repository.enroll_trial(
        trial_id=trial.trial_id,
        enrollment_capability=enrollment.capability,
        consent_notice_sha256=trial.consent_notice_sha256,
        consent_attested=True,
        now=NOW,
    )
    replay_error = _error_name(
        lambda: repository.enroll_trial(
            trial_id=trial.trial_id,
            enrollment_capability=enrollment.capability,
            consent_notice_sha256=trial.consent_notice_sha256,
            consent_attested=True,
            now=NOW,
        )
    )
    invitation = _plan(repository, participant, index=1)
    report = repository.submit(
        plan_id=invitation.plan_id,
        capability=invitation.capability,
        idempotency_key="consent-case-decision",
        phase="decision",
        value="accepted",
        now=NOW + timedelta(minutes=2),
    )
    participation = repository.authorize_trial_participant(
        capability=participant.capability,
        now=NOW + timedelta(minutes=2),
    )
    with sqlite3.connect(path) as connection:
        dump = "\n".join(connection.iterdump())
        table_names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        column_names = {
            str(row[1])
            for table_name in table_names
            for row in connection.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
        }
    return {
        "case_id": "consent_and_capability_binding",
        "wrong_consent_error": wrong_consent_error,
        "replay_error": replay_error,
        "notice": trial.consent_notice,
        "notice_sha256": trial.consent_notice_sha256,
        "participant_id": participant.participant_id,
        "authorized_participant_id": participation.participant_id,
        "report": report.to_dict(),
        "raw_capabilities_persisted": any(
            capability in dump
            for capability in (
                enrollment.capability,
                participant.capability,
                invitation.capability,
            )
        ),
        "free_text_columns_present": any(
            field in column_names
            for field in ("free_text", "comment", "email", "phone")
        ),
    }


def _isolation_case(path: Path) -> dict:
    repository = PlanFeedbackRepository(path)
    alpha = _trial(repository, tenant="alpha")
    beta = _trial(repository, tenant="beta")
    _, alpha_participant = _participant(repository, alpha, index=1)
    _, beta_participant = _participant(repository, beta, index=10)
    alpha_plan = _plan(repository, alpha_participant, index=10)
    alpha_revision = _plan(repository, alpha_participant, index=11)
    beta_plan = _plan(repository, beta_participant, index=12)
    repository.submit(
        plan_id=alpha_plan.plan_id,
        capability=alpha_plan.capability,
        idempotency_key="alpha-decision",
        phase="decision",
        value="accepted",
        now=NOW + timedelta(minutes=2),
    )
    duplicate_phase_error = _error_name(
        lambda: repository.submit(
            plan_id=alpha_revision.plan_id,
            capability=alpha_revision.capability,
            idempotency_key="alpha-revision-decision",
            phase="decision",
            value="rejected",
            reason_codes=("too_far",),
            now=NOW + timedelta(minutes=3),
        )
    )
    repository.submit(
        plan_id=beta_plan.plan_id,
        capability=beta_plan.capability,
        idempotency_key="beta-decision",
        phase="decision",
        value="rejected",
        reason_codes=("too_far",),
        now=NOW + timedelta(minutes=3),
    )
    cross_tenant_error = _error_name(
        lambda: repository.get_trial(alpha.trial_id, tenant_id="beta")
    )
    return {
        "case_id": "participant_and_cohort_isolation",
        "duplicate_phase_error": duplicate_phase_error,
        "cross_tenant_error": cross_tenant_error,
        "alpha_summary": repository.trial_summary(
            trial_id=alpha.trial_id,
            tenant_id="alpha",
            now=NOW + timedelta(minutes=4),
        ),
        "beta_summary": repository.trial_summary(
            trial_id=beta.trial_id,
            tenant_id="beta",
            now=NOW + timedelta(minutes=4),
        ),
        "uncohorted_summary": repository.public_summary(),
    }


def _withdrawal_case(path: Path) -> dict:
    repository = PlanFeedbackRepository(path)
    trial = _trial(repository)
    _, participant = _participant(repository, trial, index=1)
    invitation = _plan(repository, participant, index=20)
    repository.submit(
        plan_id=invitation.plan_id,
        capability=invitation.capability,
        idempotency_key="withdrawal-decision",
        phase="decision",
        value="accepted",
        now=NOW + timedelta(minutes=2),
    )
    event = repository.withdraw_trial(
        trial_id=trial.trial_id,
        participant_capability=participant.capability,
        now=NOW + timedelta(minutes=3),
    )
    submit_after_withdrawal_error = _error_name(
        lambda: repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key="withdrawal-outcome",
            phase="outcome",
            value="completed",
            now=NOW + timedelta(minutes=4),
        )
    )
    summary = repository.trial_summary(
        trial_id=trial.trial_id,
        now=NOW + timedelta(minutes=5),
    )
    snapshot = repository.close_trial(
        trial_id=trial.trial_id,
        tenant_id="alpha",
        closed_by="alpha-operator",
        now=NOW + timedelta(minutes=6),
    )
    authorize_after_close_error = _error_name(
        lambda: repository.authorize_trial_participant(
            capability=participant.capability,
            now=NOW + timedelta(minutes=7),
        )
    )
    participant_append_only = False
    snapshot_append_only = False
    with sqlite3.connect(path) as connection:
        try:
            connection.execute(
                "DELETE FROM trial_participants WHERE participant_id = ?",
                (participant.participant_id,),
            )
        except sqlite3.IntegrityError:
            participant_append_only = True
        try:
            connection.execute(
                "UPDATE trial_evidence_snapshots SET cutoff_at='x' WHERE trial_id = ?",
                (trial.trial_id,),
            )
        except sqlite3.IntegrityError:
            snapshot_append_only = True
    return {
        "case_id": "withdrawal_close_and_append_only",
        "withdrawal_event": event.to_dict(),
        "submit_after_withdrawal_error": submit_after_withdrawal_error,
        "authorize_after_close_error": authorize_after_close_error,
        "summary_after_withdrawal": summary,
        "snapshot": snapshot.to_dict(),
        "participant_append_only": participant_append_only,
        "snapshot_append_only": snapshot_append_only,
    }


def _minimum_snapshot_case(path: Path) -> dict:
    repository = PlanFeedbackRepository(path)
    trial = _trial(repository)
    enrollments = []
    participants = []
    reports = []
    final_invitation = None
    for index in range(TRIAL_MINIMUM_PARTICIPANTS):
        enrollment, participant = _participant(repository, trial, index=index + 1)
        invitation = _plan(repository, participant, index=index + 30)
        decision = repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key=f"minimum-decision-{index}",
            phase="decision",
            value="accepted" if index < 3 else "rejected",
            reason_codes=() if index < 3 else ("too_far",),
            now=NOW + timedelta(minutes=2, seconds=index),
        )
        enrollments.append(enrollment)
        participants.append(participant)
        reports.append(decision)
        if index < TRIAL_MINIMUM_PARTICIPANTS - 1:
            reports.append(
                repository.submit(
                    plan_id=invitation.plan_id,
                    capability=invitation.capability,
                    idempotency_key=f"minimum-outcome-{index}",
                    phase="outcome",
                    value="completed",
                    now=NOW + timedelta(minutes=3, seconds=index),
                )
            )
        else:
            final_invitation = invitation
    before = repository.trial_summary(
        trial_id=trial.trial_id,
        now=NOW + timedelta(minutes=10),
    )
    assert final_invitation is not None
    final_report = repository.submit(
        plan_id=final_invitation.plan_id,
        capability=final_invitation.capability,
        idempotency_key="minimum-outcome-final",
        phase="outcome",
        value="abandoned",
        reason_codes=("weather_issue",),
        now=NOW + timedelta(minutes=11),
    )
    reports.append(final_report)
    snapshot = repository.close_trial(
        trial_id=trial.trial_id,
        tenant_id="alpha",
        closed_by="alpha-operator",
        now=NOW + timedelta(minutes=12),
    ).to_dict()
    cohort_evidence = {
        "version": "trial_cohort_v1",
        "trial_id": trial.trial_id,
        "tenant_id": trial.tenant_id,
        "protocol_version": trial.protocol_version,
        "purpose": trial.purpose,
        "consent_notice_sha256": trial.consent_notice_sha256,
        "minimum_participants": trial.minimum_participants,
        "starts_at": trial.starts_at,
        "ends_at": trial.ends_at,
        "retention_until": trial.retention_until,
        "created_by": trial.created_by,
        "created_at": trial.created_at,
    }
    enrollment_evidence = [
        {
            "version": "trial_enrollment_invitation_v1",
            "enrollment_invitation_id": item.enrollment_invitation_id,
            "trial_id": item.trial_id,
            "issued_by": item.issued_by,
            "issued_at": item.issued_at,
            "expires_at": item.expires_at,
        }
        for item in enrollments
    ]
    participant_evidence = [
        {
            "version": "trial_participant_v1",
            "participant_id": item.participant_id,
            "trial_id": item.trial_id,
            "enrollment_invitation_id": item.enrollment_invitation_id,
            "consent_notice_sha256": item.consent_notice_sha256,
            "consented_at": item.consented_at,
            "expires_at": item.expires_at,
        }
        for item in participants
    ]
    evidence_root_input = {
        "version": "trial_evidence_root_v1",
        "trial_id": trial.trial_id,
        "cohort_sha256": trial.cohort_sha256,
        "cutoff_at": snapshot["cutoff_at"],
        "enrollment_invitation_sha256s": sorted(
            item.invitation_sha256 for item in enrollments
        ),
        "eligible_participant_sha256s": sorted(
            item.participant_sha256 for item in participants
        ),
        "withdrawal_event_sha256s": [],
        "included_report_sha256s": sorted(item.report_sha256 for item in reports),
    }
    return {
        "case_id": "minimum_gate_and_snapshot_integrity",
        "before": before,
        "snapshot": snapshot,
        "notice": trial.consent_notice,
        "notice_sha256": trial.consent_notice_sha256,
        "cohort": cohort_evidence,
        "cohort_sha256": trial.cohort_sha256,
        "enrollment_evidence": enrollment_evidence,
        "enrollment_sha256s": [item.invitation_sha256 for item in enrollments],
        "participant_evidence": participant_evidence,
        "participant_sha256s": [item.participant_sha256 for item in participants],
        "reports": [item.to_dict() for item in reports],
        "evidence_root_input": evidence_root_input,
    }


def _retention_case(path: Path) -> dict:
    repository = PlanFeedbackRepository(path)
    trial = _trial(repository, days=1)
    summary = repository.trial_summary(
        trial_id=trial.trial_id,
        now=NOW + timedelta(days=31, seconds=1),
    )
    issue_error = _error_name(
        lambda: repository.issue_trial_enrollment(
            trial_id=trial.trial_id,
            tenant_id="alpha",
            issued_by="alpha-operator",
            now=NOW + timedelta(days=1),
        )
    )
    return {
        "case_id": "retention_boundary",
        "retention_state": summary["retention_state"],
        "issue_after_collection_error": issue_error,
    }


def _retention_purge_case(path: Path) -> dict:
    repository = PlanFeedbackRepository(path)
    trial = _trial(repository, tenant="purge", days=1)
    _, participant = _participant(repository, trial, index=1)
    invitation = _plan(repository, participant, index=90)
    repository.submit(
        plan_id=invitation.plan_id,
        capability=invitation.capability,
        idempotency_key="purge-decision",
        phase="decision",
        value="accepted",
        now=NOW + timedelta(minutes=2),
    )
    repository.submit(
        plan_id=invitation.plan_id,
        capability=invitation.capability,
        idempotency_key="purge-outcome",
        phase="outcome",
        value="completed",
        now=NOW + timedelta(minutes=3),
    )
    preserved = _trial(repository, tenant="preserved")
    not_frozen_error = _error_name(
        lambda: repository.purge_trial(
            trial_id=trial.trial_id,
            tenant_id=trial.tenant_id,
            purged_by="purge-operator",
            secret_bundle_disposed=True,
            backup_disposition="no_managed_backups",
            now=NOW + timedelta(days=31, seconds=1),
        )
    )
    repository.close_trial(
        trial_id=trial.trial_id,
        tenant_id=trial.tenant_id,
        closed_by="purge-operator",
        now=NOW + timedelta(minutes=4),
    )
    not_due_error = _error_name(
        lambda: repository.purge_trial(
            trial_id=trial.trial_id,
            tenant_id=trial.tenant_id,
            purged_by="purge-operator",
            secret_bundle_disposed=True,
            backup_disposition="no_managed_backups",
            now=NOW + timedelta(days=30),
        )
    )
    tracked_tables = {
        "feedback_reports": "plan_feedback_reports",
        "feedback_invitations": "plan_feedback_invitations",
        "participant_events": "trial_participant_events",
        "participants": "trial_participants",
        "enrollment_invitations": "trial_enrollment_invitations",
        "evidence_snapshots": "trial_evidence_snapshots",
        "cohorts": "trial_cohorts",
    }
    with sqlite3.connect(path) as connection:
        before_counts = {
            name: int(
                connection.execute(
                    f'SELECT count(*) FROM "{table}" WHERE trial_id = ?',
                    (trial.trial_id,),
                ).fetchone()[0]
            )
            for name, table in tracked_tables.items()
        }
    receipt = repository.purge_trial(
        trial_id=trial.trial_id,
        tenant_id=trial.tenant_id,
        purged_by="purge-operator",
        secret_bundle_disposed=True,
        backup_disposition="no_managed_backups",
        now=NOW + timedelta(days=31, seconds=1),
    ).to_dict()
    replay = repository.purge_trial(
        trial_id=trial.trial_id,
        tenant_id=trial.tenant_id,
        purged_by="another-operator",
        secret_bundle_disposed=True,
        backup_disposition="operator_attested_backups_purged",
        now=NOW + timedelta(days=32),
    ).to_dict()
    target_lookup_error = _error_name(
        lambda: repository.get_trial(trial.trial_id, tenant_id=trial.tenant_id)
    )
    with sqlite3.connect(path) as connection:
        after_counts = {
            name: int(
                connection.execute(
                    f'SELECT count(*) FROM "{table}" WHERE trial_id = ?',
                    (trial.trial_id,),
                ).fetchone()[0]
            )
            for name, table in tracked_tables.items()
        }
        receipt_count = int(
            connection.execute(
                "SELECT count(*) FROM trial_purge_receipts WHERE trial_id = ?",
                (trial.trial_id,),
            ).fetchone()[0]
        )
        preserved_cohort_count = int(
            connection.execute(
                "SELECT count(*) FROM trial_cohorts WHERE trial_id = ?",
                (preserved.trial_id,),
            ).fetchone()[0]
        )
        foreign_key_violation_count = len(
            connection.execute("PRAGMA foreign_key_check").fetchall()
        )
        trigger_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
    expected_delete_triggers = {
        "plan_feedback_invitations_no_delete",
        "plan_feedback_reports_no_delete",
        "trial_cohorts_no_delete",
        "trial_enrollment_invitations_no_delete",
        "trial_participants_no_delete",
        "trial_participant_events_no_delete",
        "trial_evidence_snapshots_no_delete",
    }
    return {
        "case_id": "retention_purge_transaction",
        "not_frozen_error": not_frozen_error,
        "not_due_error": not_due_error,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "receipt_count": receipt_count,
        "receipt": receipt,
        "idempotent_receipt": replay == receipt,
        "target_lookup_error": target_lookup_error,
        "preserved_cohort_count": preserved_cohort_count,
        "foreign_key_violation_count": foreign_key_violation_count,
        "delete_triggers_restored": expected_delete_triggers <= trigger_names,
    }


def _metrics(cases: list[dict]) -> dict:
    indexed = {case["case_id"]: case for case in cases}
    consent = indexed["consent_and_capability_binding"]
    isolation = indexed["participant_and_cohort_isolation"]
    withdrawal = indexed["withdrawal_close_and_append_only"]
    minimum = indexed["minimum_gate_and_snapshot_integrity"]
    retention = indexed["retention_boundary"]
    purge = indexed["retention_purge_transaction"]
    snapshot = deepcopy(minimum["snapshot"])
    snapshot_sha256 = snapshot.pop("snapshot_sha256", None)
    return {
        "case_count": len(cases),
        "exact_consent_binding_rate": float(
            consent["wrong_consent_error"] == "trial_consent_mismatch"
            and consent["notice_sha256"] == _sha(consent["notice"])
            and consent["participant_id"] == consent["authorized_participant_id"]
        ),
        "single_use_enrollment_rate": float(
            consent["replay_error"] == "trial_enrollment_conflict"
        ),
        "capability_minimization_rate": float(
            consent["raw_capabilities_persisted"] is False
            and consent["free_text_columns_present"] is False
        ),
        "participant_phase_uniqueness_rate": float(
            isolation["duplicate_phase_error"] == "feedback_phase_conflict"
        ),
        "cohort_tenant_isolation_rate": float(
            isolation["cross_tenant_error"] == "trial_not_found"
            and isolation["alpha_summary"]["phase_participant_counts"]["decision"] == 1
            and isolation["beta_summary"]["phase_participant_counts"]["decision"] == 1
        ),
        "uncohorted_exclusion_rate": float(
            isolation["uncohorted_summary"]["phase_counts"]
            == {"decision": 0, "outcome": 0}
        ),
        "withdrawal_exclusion_rate": float(
            withdrawal["submit_after_withdrawal_error"]
            == "trial_participant_withdrawn"
            and withdrawal["summary_after_withdrawal"]["included_participant_count"] == 0
            and withdrawal["summary_after_withdrawal"]["phase_participant_counts"]
            == {"decision": 0, "outcome": 0}
        ),
        "closure_fail_closed_rate": float(
            withdrawal["authorize_after_close_error"] == "trial_closed"
        ),
        "append_only_rate": float(
            withdrawal["participant_append_only"] is True
            and withdrawal["snapshot_append_only"] is True
        ),
        "minimum_participant_gate_rate": float(
            minimum["before"]["decision_acceptance_rate"] == 0.6
            and minimum["before"]["outcome_completion_rate"] is None
            and minimum["snapshot"]["outcome_completion_rate"] == 0.8
        ),
        "snapshot_integrity_rate": float(
            snapshot_sha256 == _sha(snapshot)
            and minimum["snapshot"]["evidence_root_sha256"]
            == _sha(minimum["evidence_root_input"])
        ),
        "retention_boundary_rate": float(
            retention["retention_state"] == "raw_purge_due"
            and retention["issue_after_collection_error"] == "trial_not_active"
        ),
        "retention_purge_rate": float(
            purge["not_frozen_error"] == "trial_purge_not_ready"
            and purge["not_due_error"] == "trial_retention_not_due"
            and purge["before_counts"] == purge["receipt"]["deleted_counts"]
            and all(value == 0 for value in purge["after_counts"].values())
            and purge["receipt_count"] == 1
            and purge["target_lookup_error"] == "trial_not_found"
            and purge["preserved_cohort_count"] == 1
            and purge["foreign_key_violation_count"] == 0
            and purge["delete_triggers_restored"] is True
            and purge["idempotent_receipt"] is True
        ),
    }


def evaluate_trials() -> dict:
    with TemporaryDirectory(prefix="bj-pal-trials-eval-") as directory:
        base = Path(directory)
        cases = [
            _consent_case(base / "consent.db"),
            _isolation_case(base / "isolation.db"),
            _withdrawal_case(base / "withdrawal.db"),
            _minimum_snapshot_case(base / "minimum.db"),
            _retention_case(base / "retention.db"),
            _retention_purge_case(base / "retention-purge.db"),
        ]
    artifact = {
        "schema_version": 1,
        "classification": "synthetic_contract",
        "policy": {
            "protocol_version": "bj_pal_trial_protocol_v1",
            "classification": FEEDBACK_CLASSIFICATION,
            "minimum_participants": TRIAL_MINIMUM_PARTICIPANTS,
            "enrollment": "operator_issued_single_use_capability",
            "participant_identity": "anonymous_capability_not_verified_human",
            "report_uniqueness": "trial_participant_phase",
            "withdrawal": "exclude_unfrozen_aggregate_and_block_new_evidence",
            "retention": "explicit_local_operator_purge_without_hosted_scheduler",
        },
        "result": {"raw_cases": cases, "metrics": _metrics(cases)},
    }
    artifact["artifact_sha256"] = _sha(artifact)
    return artifact


def write_artifact(path: Path, artifact: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
