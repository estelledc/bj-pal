from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from outcomes import (  # noqa: E402
    FeedbackExpired,
    FeedbackIdempotencyConflict,
    FeedbackIntegrityError,
    FeedbackNotFound,
    FeedbackPhaseConflict,
    PlanFeedbackRepository,
    sha256_json,
)


def _issue(
    repository: PlanFeedbackRepository,
    *,
    plan_id: str = "plan-feedback",
    artifact: str = "a" * 64,
    now: datetime | None = None,
):
    return repository.issue(
        plan_id=plan_id,
        plan_artifact_sha256=artifact,
        data_profile_name="demo",
        data_profile_classification="synthetic",
        now=now,
    )


def test_capability_binds_plan_and_artifact_without_persisting_raw_secret(tmp_path) -> None:
    path = tmp_path / "feedback.db"
    repository = PlanFeedbackRepository(path)
    first = _issue(repository, artifact="a" * 64)
    second = _issue(repository, artifact="b" * 64)

    first_report = repository.submit(
        plan_id="plan-feedback",
        capability=first.capability,
        idempotency_key="decision-first",
        phase="decision",
        value="accepted",
    )
    second_report = repository.submit(
        plan_id="plan-feedback",
        capability=second.capability,
        idempotency_key="decision-second",
        phase="decision",
        value="rejected",
        reason_codes=("too_far",),
    )

    assert first_report.plan_artifact_sha256 == "a" * 64
    assert second_report.plan_artifact_sha256 == "b" * 64
    assert repository.list_reports(
        plan_id="plan-feedback", capability=first.capability
    ) == (first_report,)
    with pytest.raises(FeedbackNotFound):
        repository.list_reports(plan_id="another-plan", capability=first.capability)

    with sqlite3.connect(path) as connection:
        dump = "\n".join(connection.iterdump())
    assert first.capability not in dump
    assert second.capability not in dump
    assert "capability_sha256" in dump


def test_reports_are_idempotent_immutable_and_self_checking(tmp_path) -> None:
    repository = PlanFeedbackRepository(tmp_path / "feedback.db")
    invitation = _issue(repository)
    report = repository.submit(
        plan_id=invitation.plan_id,
        capability=invitation.capability,
        idempotency_key="same-request",
        phase="decision",
        value="requested_change",
        reason_codes=("route_issue",),
    )
    replay = repository.submit(
        plan_id=invitation.plan_id,
        capability=invitation.capability,
        idempotency_key="same-request",
        phase="decision",
        value="requested_change",
        reason_codes=("route_issue",),
    )
    assert replay == report

    with pytest.raises(FeedbackIdempotencyConflict):
        repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key="same-request",
            phase="decision",
            value="rejected",
            reason_codes=("too_far",),
        )
    with pytest.raises(FeedbackPhaseConflict):
        repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key="different-request",
            phase="decision",
            value="rejected",
            reason_codes=("too_far",),
        )

    evidence = report.to_dict()
    stored_sha256 = evidence.pop("report_sha256")
    assert sha256_json(evidence) == stored_sha256
    with sqlite3.connect(repository.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE plan_feedback_reports SET value='rejected' WHERE feedback_id=?",
                (report.feedback_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM plan_feedback_invitations WHERE invitation_id=?",
                (invitation.invitation_id,),
            )

    with sqlite3.connect(repository.path) as connection:
        connection.execute("DROP TRIGGER plan_feedback_reports_no_update")
        connection.execute(
            "UPDATE plan_feedback_reports SET value='rejected' WHERE feedback_id=?",
            (report.feedback_id,),
        )
    with pytest.raises(FeedbackIntegrityError, match="hash mismatch"):
        repository.public_summary()


def test_schema_minimization_and_expiry_fail_closed(tmp_path) -> None:
    repository = PlanFeedbackRepository(tmp_path / "feedback.db")
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    invitation = repository.issue(
        plan_id="plan-expiry",
        plan_artifact_sha256="c" * 64,
        data_profile_name="demo",
        data_profile_classification="synthetic",
        ttl_seconds=300,
        now=now,
    )

    with pytest.raises(ValueError, match="requires at least one reason"):
        repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key="missing-reason",
            phase="outcome",
            value="abandoned",
            now=now,
        )
    with pytest.raises(ValueError, match="unsupported reason"):
        repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key="free-text",
            phase="outcome",
            value="abandoned",
            reason_codes=("my phone number is 123",),
            now=now,
        )
    with pytest.raises(FeedbackExpired):
        repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key="expired",
            phase="decision",
            value="accepted",
            now=now + timedelta(seconds=301),
        )


def test_public_rates_stay_hidden_until_each_phase_reaches_minimum(tmp_path) -> None:
    repository = PlanFeedbackRepository(tmp_path / "feedback.db")
    for index in range(5):
        invitation = _issue(
            repository,
            plan_id=f"plan-summary-{index}",
            artifact=f"{index + 1:064x}",
        )
        repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key=f"decision-{index}",
            phase="decision",
            value="accepted" if index < 3 else "rejected",
            reason_codes=() if index < 3 else ("too_far",),
        )
        if index < 4:
            repository.submit(
                plan_id=invitation.plan_id,
                capability=invitation.capability,
                idempotency_key=f"outcome-{index}",
                phase="outcome",
                value="completed",
            )

    summary = repository.public_summary()
    assert summary["evidence_level"] == "aggregate_self_reported"
    assert summary["phase_counts"] == {"decision": 5, "outcome": 4}
    assert summary["decision_acceptance_rate"] == 0.6
    assert summary["outcome_completion_rate"] is None
    assert "accepted" in summary["value_counts"]
    assert "completed" not in summary["value_counts"]
    assert summary["classification"] == "self_reported_unverified"
