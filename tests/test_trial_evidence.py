from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from outcomes import (  # noqa: E402
    FeedbackPhaseConflict,
    PlanFeedbackRepository,
    TrialClosed,
    TrialConsentMismatch,
    TrialEnrollmentConflict,
    TrialIntegrityError,
    TrialNotActive,
    TrialNotFound,
    TrialParticipantWithdrawn,
    TrialPurgeNotReady,
    TrialRetentionNotDue,
)
import outcomes.repository as outcome_repository  # noqa: E402


NOW = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)


def _trial(repository: PlanFeedbackRepository, *, minimum: int = 5):
    return repository.create_trial(
        created_by="trial-operator",
        minimum_participants=minimum,
        now=NOW,
    )


def _participant(repository: PlanFeedbackRepository, trial, *, index: int = 0):
    invitation = repository.issue_trial_enrollment(
        trial_id=trial.trial_id,
        issued_by="trial-operator",
        now=NOW + timedelta(seconds=index),
    )
    participant = repository.enroll_trial(
        trial_id=trial.trial_id,
        enrollment_capability=invitation.capability,
        consent_notice_sha256=trial.consent_notice_sha256,
        consent_attested=True,
        now=NOW + timedelta(seconds=index + 1),
    )
    return invitation, participant


def _plan_invitation(
    repository: PlanFeedbackRepository,
    participant,
    *,
    index: int = 0,
    artifact: str | None = None,
):
    return repository.issue(
        plan_id=f"trial-plan-{index}",
        plan_artifact_sha256=artifact or f"{index + 1:064x}",
        data_profile_name="demo",
        data_profile_classification="synthetic",
        trial_participant_capability=participant.capability,
        now=NOW + timedelta(minutes=1, seconds=index),
    )


def test_exact_consent_single_use_and_capability_minimization(tmp_path) -> None:
    path = tmp_path / "feedback.db"
    repository = PlanFeedbackRepository(path)
    trial = _trial(repository)
    enrollment = repository.issue_trial_enrollment(
        trial_id=trial.trial_id,
        issued_by="trial-operator",
        now=NOW,
    )

    with pytest.raises(TrialConsentMismatch):
        repository.enroll_trial(
            trial_id=trial.trial_id,
            enrollment_capability=enrollment.capability,
            consent_notice_sha256="0" * 64,
            consent_attested=True,
            now=NOW,
        )
    with pytest.raises(TrialConsentMismatch):
        repository.enroll_trial(
            trial_id=trial.trial_id,
            enrollment_capability=enrollment.capability,
            consent_notice_sha256=trial.consent_notice_sha256,
            consent_attested=False,
            now=NOW,
        )

    participant = repository.enroll_trial(
        trial_id=trial.trial_id,
        enrollment_capability=enrollment.capability,
        consent_notice_sha256=trial.consent_notice_sha256,
        consent_attested=True,
        now=NOW,
    )
    with pytest.raises(TrialEnrollmentConflict):
        repository.enroll_trial(
            trial_id=trial.trial_id,
            enrollment_capability=enrollment.capability,
            consent_notice_sha256=trial.consent_notice_sha256,
            consent_attested=True,
            now=NOW,
        )

    plan_invitation = _plan_invitation(repository, participant)
    report = repository.submit(
        plan_id=plan_invitation.plan_id,
        capability=plan_invitation.capability,
        idempotency_key="trial-decision",
        phase="decision",
        value="accepted",
        now=NOW + timedelta(minutes=2),
    )
    assert plan_invitation.version == "feedback_invitation_v2"
    assert report.version == "plan_feedback_report_v2"
    assert report.trial_id == trial.trial_id
    assert report.participant_id == participant.participant_id
    assert report.consent_notice_sha256 == trial.consent_notice_sha256

    with sqlite3.connect(path) as connection:
        dump = "\n".join(connection.iterdump())
    assert enrollment.capability not in dump
    assert participant.capability not in dump
    assert plan_invitation.capability not in dump
    assert "free_text" not in dump


def test_enrollment_batch_is_bounded_unique_and_transactional(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "feedback.db"
    repository = PlanFeedbackRepository(path)
    trial = _trial(repository)
    invitations = repository.issue_trial_enrollments(
        trial_id=trial.trial_id,
        issued_by="trial-operator",
        count=3,
        now=NOW,
    )
    assert len(invitations) == 3
    assert len({item.capability for item in invitations}) == 3
    assert len({item.enrollment_invitation_id for item in invitations}) == 3
    assert all(item.issued_at == invitations[0].issued_at for item in invitations)

    monkeypatch.setattr(
        outcome_repository.secrets,
        "token_urlsafe",
        lambda _: "forced-duplicate-capability",
    )
    with pytest.raises(sqlite3.IntegrityError):
        repository.issue_trial_enrollments(
            trial_id=trial.trial_id,
            issued_by="trial-operator",
            count=2,
            now=NOW,
        )
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM trial_enrollment_invitations"
        ).fetchone()[0] == 3
    assert not any(
        item.capability.encode("utf-8") in path.read_bytes()
        for item in invitations
    )


def test_participant_phase_is_unique_across_plan_revisions_and_cohorts_are_isolated(
    tmp_path,
) -> None:
    repository = PlanFeedbackRepository(tmp_path / "feedback.db")
    first_trial = _trial(repository)
    second_trial = _trial(repository)
    _, first_participant = _participant(repository, first_trial)
    _, second_participant = _participant(repository, second_trial, index=10)
    first_plan = _plan_invitation(repository, first_participant, index=1)
    revised_plan = _plan_invitation(repository, first_participant, index=2)
    second_plan = _plan_invitation(repository, second_participant, index=3)

    repository.submit(
        plan_id=first_plan.plan_id,
        capability=first_plan.capability,
        idempotency_key="first-trial-decision",
        phase="decision",
        value="accepted",
        now=NOW + timedelta(minutes=2),
    )
    with pytest.raises(FeedbackPhaseConflict, match="participant"):
        repository.submit(
            plan_id=revised_plan.plan_id,
            capability=revised_plan.capability,
            idempotency_key="revised-trial-decision",
            phase="decision",
            value="rejected",
            reason_codes=("too_far",),
            now=NOW + timedelta(minutes=3),
        )
    repository.submit(
        plan_id=second_plan.plan_id,
        capability=second_plan.capability,
        idempotency_key="second-trial-decision",
        phase="decision",
        value="rejected",
        reason_codes=("too_far",),
        now=NOW + timedelta(minutes=3),
    )

    first_summary = repository.trial_summary(
        trial_id=first_trial.trial_id,
        now=NOW + timedelta(minutes=4),
    )
    second_summary = repository.trial_summary(
        trial_id=second_trial.trial_id,
        now=NOW + timedelta(minutes=4),
    )
    assert first_summary["phase_participant_counts"]["decision"] == 1
    assert second_summary["phase_participant_counts"]["decision"] == 1
    assert repository.public_summary()["phase_counts"] == {
        "decision": 0,
        "outcome": 0,
    }


def test_withdrawal_excludes_evidence_and_blocks_future_reports(tmp_path) -> None:
    repository = PlanFeedbackRepository(tmp_path / "feedback.db")
    trial = _trial(repository)
    _, participant = _participant(repository, trial)
    plan_invitation = _plan_invitation(repository, participant)
    repository.submit(
        plan_id=plan_invitation.plan_id,
        capability=plan_invitation.capability,
        idempotency_key="decision-before-withdrawal",
        phase="decision",
        value="accepted",
        now=NOW + timedelta(minutes=2),
    )

    event = repository.withdraw_trial(
        trial_id=trial.trial_id,
        participant_capability=participant.capability,
        now=NOW + timedelta(minutes=3),
    )
    replay = repository.withdraw_trial(
        trial_id=trial.trial_id,
        participant_capability=participant.capability,
        now=NOW + timedelta(minutes=3),
    )
    assert replay == event
    with pytest.raises(TrialParticipantWithdrawn):
        repository.submit(
            plan_id=plan_invitation.plan_id,
            capability=plan_invitation.capability,
            idempotency_key="outcome-after-withdrawal",
            phase="outcome",
            value="completed",
            now=NOW + timedelta(minutes=4),
        )
    summary = repository.trial_summary(
        trial_id=trial.trial_id,
        now=NOW + timedelta(minutes=5),
    )
    assert summary["withdrawn_participant_count"] == 1
    assert summary["eligible_participant_count"] == 0
    assert summary["included_participant_count"] == 0
    assert summary["phase_participant_counts"]["decision"] == 0


def test_minimum_distinct_participant_gate_and_frozen_snapshot(tmp_path) -> None:
    repository = PlanFeedbackRepository(tmp_path / "feedback.db")
    trial = _trial(repository)
    final_plan = None
    for index in range(5):
        _, participant = _participant(repository, trial, index=index * 2)
        plan_invitation = _plan_invitation(
            repository,
            participant,
            index=index + 20,
        )
        repository.submit(
            plan_id=plan_invitation.plan_id,
            capability=plan_invitation.capability,
            idempotency_key=f"decision-{index}",
            phase="decision",
            value="accepted" if index < 3 else "rejected",
            reason_codes=() if index < 3 else ("too_far",),
            now=NOW + timedelta(minutes=2, seconds=index),
        )
        if index < 4:
            repository.submit(
                plan_id=plan_invitation.plan_id,
                capability=plan_invitation.capability,
                idempotency_key=f"outcome-{index}",
                phase="outcome",
                value="completed",
                now=NOW + timedelta(minutes=3, seconds=index),
            )
        else:
            final_plan = plan_invitation

    before = repository.trial_summary(
        trial_id=trial.trial_id,
        now=NOW + timedelta(minutes=10),
    )
    assert before["decision_acceptance_rate"] == 0.6
    assert before["outcome_completion_rate"] is None
    assert "accepted" in before["value_counts"]
    assert "completed" not in before["value_counts"]

    assert final_plan is not None
    repository.submit(
        plan_id=final_plan.plan_id,
        capability=final_plan.capability,
        idempotency_key="outcome-final",
        phase="outcome",
        value="abandoned",
        reason_codes=("weather_issue",),
        now=NOW + timedelta(minutes=11),
    )
    snapshot = repository.close_trial(
        trial_id=trial.trial_id,
        closed_by="trial-operator",
        now=NOW + timedelta(minutes=12),
    )
    payload = snapshot.to_dict()
    assert payload["phase_participant_counts"] == {"decision": 5, "outcome": 5}
    assert payload["decision_acceptance_rate"] == 0.6
    assert payload["outcome_completion_rate"] == 0.8
    assert payload["evidence_level"] == "aggregate_self_reported"
    assert repository.close_trial(
        trial_id=trial.trial_id,
        closed_by="another-operator",
        now=NOW + timedelta(minutes=13),
    ) == snapshot
    with pytest.raises(TrialClosed):
        repository.authorize_trial_participant(
            capability=participant.capability,
            now=NOW + timedelta(minutes=13),
        )


def test_trial_hash_chain_and_retention_signal_fail_closed(tmp_path) -> None:
    path = tmp_path / "feedback.db"
    repository = PlanFeedbackRepository(path)
    trial = repository.create_trial(
        created_by="trial-operator",
        duration_days=1,
        retention_days=1,
        now=NOW,
    )
    due = repository.trial_summary(
        trial_id=trial.trial_id,
        now=NOW + timedelta(days=2, seconds=1),
    )
    assert due["retention_state"] == "raw_purge_due"

    with pytest.raises(TrialNotActive):
        repository.issue_trial_enrollment(
            trial_id=trial.trial_id,
            issued_by="trial-operator",
            now=NOW + timedelta(days=1),
        )

    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER trial_cohorts_no_update")
        connection.execute(
            "UPDATE trial_cohorts SET created_by='tampered' WHERE trial_id=?",
            (trial.trial_id,),
        )
    with pytest.raises(TrialIntegrityError):
        repository.get_trial(trial.trial_id)


def test_retention_purge_is_gated_isolated_idempotent_and_receipted(tmp_path) -> None:
    path = tmp_path / "feedback.db"
    repository = PlanFeedbackRepository(path)
    trial = repository.create_trial(
        created_by="trial-operator",
        tenant_id="purge-tenant",
        duration_days=1,
        retention_days=1,
        now=NOW,
    )
    enrollment, participant = _participant(repository, trial)
    plan_invitation = _plan_invitation(repository, participant, index=20)
    repository.submit(
        plan_id=plan_invitation.plan_id,
        capability=plan_invitation.capability,
        idempotency_key="purge-decision",
        phase="decision",
        value="accepted",
        now=NOW + timedelta(minutes=2),
    )
    repository.submit(
        plan_id=plan_invitation.plan_id,
        capability=plan_invitation.capability,
        idempotency_key="purge-outcome",
        phase="outcome",
        value="completed",
        now=NOW + timedelta(minutes=3),
    )
    repository.withdraw_trial(
        trial_id=trial.trial_id,
        participant_capability=participant.capability,
        now=NOW + timedelta(minutes=4),
    )

    preserved_trial = _trial(repository)
    preserved_enrollment = repository.issue_trial_enrollment(
        trial_id=preserved_trial.trial_id,
        issued_by="trial-operator",
        now=NOW,
    )
    legacy_invitation = repository.issue(
        plan_id="legacy-plan",
        plan_artifact_sha256="f" * 64,
        data_profile_name="demo",
        data_profile_classification="synthetic",
        now=NOW,
    )
    repository.submit(
        plan_id=legacy_invitation.plan_id,
        capability=legacy_invitation.capability,
        idempotency_key="legacy-decision",
        phase="decision",
        value="accepted",
        now=NOW + timedelta(minutes=1),
    )

    with pytest.raises(TrialPurgeNotReady):
        repository.purge_trial(
            trial_id=trial.trial_id,
            tenant_id=trial.tenant_id,
            purged_by="trial-operator",
            secret_bundle_disposed=True,
            backup_disposition="no_managed_backups",
            now=NOW + timedelta(days=2, seconds=1),
        )
    snapshot = repository.close_trial(
        trial_id=trial.trial_id,
        closed_by="trial-operator",
        now=NOW + timedelta(minutes=5),
    )
    with pytest.raises(TrialRetentionNotDue):
        repository.purge_trial(
            trial_id=trial.trial_id,
            tenant_id=trial.tenant_id,
            purged_by="trial-operator",
            secret_bundle_disposed=True,
            backup_disposition="no_managed_backups",
            now=NOW + timedelta(days=1),
        )
    with pytest.raises(ValueError, match="explicitly attested"):
        repository.purge_trial(
            trial_id=trial.trial_id,
            tenant_id=trial.tenant_id,
            purged_by="trial-operator",
            secret_bundle_disposed=False,
            backup_disposition="no_managed_backups",
            now=NOW + timedelta(days=2, seconds=1),
        )
    with pytest.raises(ValueError, match="backup_disposition"):
        repository.purge_trial(
            trial_id=trial.trial_id,
            tenant_id=trial.tenant_id,
            purged_by="trial-operator",
            secret_bundle_disposed=True,
            backup_disposition="unknown",
            now=NOW + timedelta(days=2, seconds=1),
        )

    receipt = repository.purge_trial(
        trial_id=trial.trial_id,
        tenant_id=trial.tenant_id,
        purged_by="trial-operator",
        secret_bundle_disposed=True,
        backup_disposition="no_managed_backups",
        now=NOW + timedelta(days=2, seconds=1),
    )
    payload = receipt.to_dict()
    assert payload["snapshot_sha256"] == snapshot.snapshot_sha256
    assert payload["classification"] == "operator_attested_unverified"
    assert "tenant_id" not in payload
    assert "purged_by" not in payload
    assert len(payload["tenant_sha256"]) == 64
    assert len(payload["purged_by_sha256"]) == 64
    assert payload["sqlite_deletion_controls"] == {
        "journal_mode": "delete",
        "secure_delete": True,
    }
    assert payload["deleted_counts"] == {
        "feedback_reports": 2,
        "feedback_invitations": 1,
        "participant_events": 1,
        "participants": 1,
        "enrollment_invitations": 1,
        "evidence_snapshots": 1,
        "cohorts": 1,
    }
    assert enrollment.capability not in str(payload)
    assert participant.capability not in str(payload)
    assert plan_invitation.capability not in str(payload)
    with pytest.raises(TrialNotFound):
        repository.get_trial(trial.trial_id, tenant_id=trial.tenant_id)
    assert repository.get_trial_purge_receipt(
        trial.trial_id,
        tenant_id=trial.tenant_id,
    ) == receipt
    assert repository.purge_trial(
        trial_id=trial.trial_id,
        tenant_id=trial.tenant_id,
        purged_by="another-operator",
        secret_bundle_disposed=True,
        backup_disposition="operator_attested_backups_purged",
        now=NOW + timedelta(days=3),
    ) == receipt

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute(
            "SELECT count(*) FROM trial_cohorts WHERE trial_id = ?",
            (preserved_trial.trial_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM trial_enrollment_invitations "
            "WHERE enrollment_invitation_id = ?",
            (preserved_enrollment.enrollment_invitation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM plan_feedback_reports WHERE plan_id = 'legacy-plan'"
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        trigger_names = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
    assert set(outcome_repository.RETENTION_PURGE_DELETE_TRIGGERS) <= trigger_names


def test_retention_purge_rolls_back_deletes_and_trigger_ddl(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "feedback.db"
    repository = PlanFeedbackRepository(path)
    trial = repository.create_trial(
        created_by="trial-operator",
        tenant_id="rollback-tenant",
        duration_days=1,
        retention_days=1,
        now=NOW,
    )
    repository.issue_trial_enrollment(
        trial_id=trial.trial_id,
        issued_by="trial-operator",
        now=NOW,
    )
    repository.close_trial(
        trial_id=trial.trial_id,
        closed_by="trial-operator",
        now=NOW + timedelta(minutes=1),
    )
    monkeypatch.setitem(
        outcome_repository.RETENTION_PURGE_DELETE_TRIGGERS,
        "trial_evidence_snapshots_no_delete",
        "CREATE TRIGGER invalid retention purge SQL",
    )

    with pytest.raises(sqlite3.OperationalError):
        repository.purge_trial(
            trial_id=trial.trial_id,
            tenant_id=trial.tenant_id,
            purged_by="trial-operator",
            secret_bundle_disposed=True,
            backup_disposition="no_managed_backups",
            now=NOW + timedelta(days=2, seconds=1),
        )

    assert repository.get_trial(trial.trial_id).status == "closed"
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM trial_enrollment_invitations WHERE trial_id = ?",
            (trial.trial_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM trial_evidence_snapshots WHERE trial_id = ?",
            (trial.trial_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM trial_purge_receipts WHERE trial_id = ?",
            (trial.trial_id,),
        ).fetchone()[0] == 0
        trigger_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
    assert set(outcome_repository.RETENTION_PURGE_DELETE_TRIGGERS) <= trigger_names


def test_retention_purge_receipt_tamper_fails_closed(tmp_path) -> None:
    path = tmp_path / "feedback.db"
    repository = PlanFeedbackRepository(path)
    trial = repository.create_trial(
        created_by="trial-operator",
        tenant_id="receipt-tenant",
        duration_days=1,
        retention_days=1,
        now=NOW,
    )
    repository.close_trial(
        trial_id=trial.trial_id,
        closed_by="trial-operator",
        now=NOW,
    )
    repository.purge_trial(
        trial_id=trial.trial_id,
        tenant_id=trial.tenant_id,
        purged_by="trial-operator",
        secret_bundle_disposed=True,
        backup_disposition="no_managed_backups",
        now=NOW + timedelta(days=2, seconds=1),
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER trial_purge_receipts_no_update")
        connection.execute(
            "UPDATE trial_purge_receipts SET purged_at = '2020-01-01T00:00:00.000Z' "
            "WHERE trial_id = ?",
            (trial.trial_id,),
        )
    with pytest.raises(TrialIntegrityError):
        repository.get_trial_purge_receipt(trial.trial_id)


def test_retention_purge_rejects_wal_without_deleting_live_rows(tmp_path) -> None:
    path = tmp_path / "feedback.db"
    repository = PlanFeedbackRepository(path)
    trial = repository.create_trial(
        created_by="trial-operator",
        tenant_id="wal-tenant",
        duration_days=1,
        retention_days=1,
        now=NOW,
    )
    repository.close_trial(
        trial_id=trial.trial_id,
        closed_by="trial-operator",
        now=NOW,
    )
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"

    with pytest.raises(TrialPurgeNotReady, match="journal_mode"):
        repository.purge_trial(
            trial_id=trial.trial_id,
            tenant_id=trial.tenant_id,
            purged_by="trial-operator",
            secret_bundle_disposed=True,
            backup_disposition="no_managed_backups",
            now=NOW + timedelta(days=2, seconds=1),
        )

    assert repository.get_trial(trial.trial_id).status == "closed"
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM trial_evidence_snapshots WHERE trial_id = ?",
            (trial.trial_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM trial_purge_receipts WHERE trial_id = ?",
            (trial.trial_id,),
        ).fetchone()[0] == 0
