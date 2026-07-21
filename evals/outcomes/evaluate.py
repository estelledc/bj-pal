"""Generate raw evidence for the plan feedback contract."""

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
    MINIMUM_PHASE_SAMPLES,
    REASON_CODES,
    FeedbackExpired,
    FeedbackIdempotencyConflict,
    FeedbackNotFound,
    FeedbackPhaseConflict,
    PlanFeedbackRepository,
)


def _sha(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _error_name(callback) -> str | None:
    try:
        callback()
    except FeedbackNotFound:
        return "feedback_not_found"
    except FeedbackExpired:
        return "feedback_expired"
    except FeedbackIdempotencyConflict:
        return "feedback_idempotency_conflict"
    except FeedbackPhaseConflict:
        return "feedback_phase_conflict"
    except ValueError:
        return "invalid_feedback"
    return None


def _issue(
    repository: PlanFeedbackRepository,
    *,
    plan_id: str,
    artifact: str,
    now: datetime | None = None,
    ttl_seconds: int = 14 * 24 * 60 * 60,
):
    return repository.issue(
        plan_id=plan_id,
        plan_artifact_sha256=artifact,
        data_profile_name="demo",
        data_profile_classification="synthetic",
        ttl_seconds=ttl_seconds,
        now=now,
    )

def _binding_case(path: Path) -> dict:
    repository = PlanFeedbackRepository(path)
    invitation = _issue(
        repository,
        plan_id="eval-binding-plan",
        artifact="a" * 64,
    )
    report = repository.submit(
        plan_id=invitation.plan_id,
        capability=invitation.capability,
        idempotency_key="eval-binding-decision",
        phase="decision",
        value="accepted",
    )
    mismatch_error = _error_name(
        lambda: repository.list_reports(
            plan_id="eval-other-plan",
            capability=invitation.capability,
        )
    )
    with sqlite3.connect(path) as connection:
        dump = "\n".join(connection.iterdump())
    invitation_evidence = {
        "version": "feedback_invitation_v1",
        "invitation_id": invitation.invitation_id,
        "plan_id": invitation.plan_id,
        "plan_artifact_sha256": invitation.plan_artifact_sha256,
        "data_profile_name": invitation.data_profile_name,
        "data_profile_classification": invitation.data_profile_classification,
        "classification": invitation.classification,
        "issued_at": invitation.issued_at,
        "expires_at": invitation.expires_at,
    }
    return {
        "case_id": "capability_and_artifact_binding",
        "plan_id": invitation.plan_id,
        "plan_artifact_sha256": invitation.plan_artifact_sha256,
        "invitation": invitation_evidence,
        "invitation_sha256": invitation.invitation_sha256,
        "report": report.to_dict(),
        "mismatch_error": mismatch_error,
        "raw_capability_persisted": invitation.capability in dump,
        "free_text_columns_present": any(
            column in dump for column in ("free_text", "comment", "phone", "email")
        ),
    }


def _idempotency_case(path: Path) -> dict:
    repository = PlanFeedbackRepository(path)
    invitation = _issue(
        repository,
        plan_id="eval-idempotency-plan",
        artifact="b" * 64,
    )
    kwargs = {
        "plan_id": invitation.plan_id,
        "capability": invitation.capability,
        "idempotency_key": "eval-idempotency-key",
        "phase": "decision",
        "value": "requested_change",
        "reason_codes": ("route_issue",),
    }
    first = repository.submit(**kwargs)
    replay = repository.submit(**kwargs)
    idempotency_error = _error_name(
        lambda: repository.submit(
            **{
                **kwargs,
                "value": "rejected",
                "reason_codes": ("too_far",),
            }
        )
    )
    phase_error = _error_name(
        lambda: repository.submit(
            **{
                **kwargs,
                "idempotency_key": "eval-second-key",
                "value": "rejected",
                "reason_codes": ("too_far",),
            }
        )
    )
    phase_value_error = _error_name(
        lambda: repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key="eval-wrong-phase",
            phase="outcome",
            value="accepted",
        )
    )
    reason_required_error = _error_name(
        lambda: repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key="eval-missing-reason",
            phase="outcome",
            value="abandoned",
        )
    )
    free_text_error = _error_name(
        lambda: repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key="eval-free-text",
            phase="outcome",
            value="abandoned",
            reason_codes=("call me at 123",),
        )
    )
    return {
        "case_id": "idempotency_and_schema",
        "first_feedback_id": first.feedback_id,
        "replay_feedback_id": replay.feedback_id,
        "idempotency_error": idempotency_error,
        "phase_error": phase_error,
        "phase_value_error": phase_value_error,
        "reason_required_error": reason_required_error,
        "free_text_error": free_text_error,
    }


def _expiry_append_only_case(path: Path) -> dict:
    repository = PlanFeedbackRepository(path)
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    invitation = _issue(
        repository,
        plan_id="eval-expiry-plan",
        artifact="c" * 64,
        now=now,
        ttl_seconds=300,
    )
    expiry_error = _error_name(
        lambda: repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key="eval-expired",
            phase="decision",
            value="accepted",
            now=now + timedelta(seconds=301),
        )
    )
    live = _issue(
        repository,
        plan_id="eval-append-plan",
        artifact="d" * 64,
        now=now,
    )
    report = repository.submit(
        plan_id=live.plan_id,
        capability=live.capability,
        idempotency_key="eval-append",
        phase="decision",
        value="accepted",
        now=now,
    )
    invitation_append_only = False
    report_append_only = False
    with sqlite3.connect(path) as connection:
        try:
            connection.execute(
                "DELETE FROM plan_feedback_invitations WHERE invitation_id = ?",
                (live.invitation_id,),
            )
        except sqlite3.IntegrityError:
            invitation_append_only = True
        try:
            connection.execute(
                "UPDATE plan_feedback_reports SET value='rejected' WHERE feedback_id = ?",
                (report.feedback_id,),
            )
        except sqlite3.IntegrityError:
            report_append_only = True
        report_count = connection.execute(
            "SELECT COUNT(*) FROM plan_feedback_reports WHERE plan_id = ?",
            (invitation.plan_id,),
        ).fetchone()[0]
    return {
        "case_id": "expiry_and_append_only",
        "expiry_error": expiry_error,
        "expired_report_count": report_count,
        "invitation_append_only": invitation_append_only,
        "report_append_only": report_append_only,
    }


def _minimum_sample_case(path: Path) -> dict:
    repository = PlanFeedbackRepository(path)
    for index in range(MINIMUM_PHASE_SAMPLES):
        invitation = _issue(
            repository,
            plan_id=f"eval-summary-{index}",
            artifact=f"{index + 10:064x}",
        )
        repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key=f"eval-decision-{index}",
            phase="decision",
            value="accepted" if index < 3 else "rejected",
            reason_codes=() if index < 3 else ("too_far",),
        )
        if index < MINIMUM_PHASE_SAMPLES - 1:
            repository.submit(
                plan_id=invitation.plan_id,
                capability=invitation.capability,
                idempotency_key=f"eval-outcome-{index}",
                phase="outcome",
                value="completed",
            )
        else:
            final_invitation = invitation
    before = repository.public_summary()
    repository.submit(
        plan_id=final_invitation.plan_id,
        capability=final_invitation.capability,
        idempotency_key="eval-outcome-final",
        phase="outcome",
        value="abandoned",
        reason_codes=("weather_issue",),
    )
    after = repository.public_summary()
    return {
        "case_id": "minimum_sample_gate",
        "before": before,
        "after": after,
    }


def _metrics(cases: list[dict]) -> dict:
    indexed = {case["case_id"]: case for case in cases}
    binding = indexed["capability_and_artifact_binding"]
    idempotency = indexed["idempotency_and_schema"]
    expiry = indexed["expiry_and_append_only"]
    sample = indexed["minimum_sample_gate"]
    report = deepcopy(binding["report"])
    report_sha256 = report.pop("report_sha256", None)
    return {
        "case_count": len(cases),
        "capability_binding_rate": float(
            binding["mismatch_error"] == "feedback_not_found"
            and binding["plan_artifact_sha256"] == binding["report"]["plan_artifact_sha256"]
        ),
        "artifact_integrity_rate": float(
            binding["invitation_sha256"] == _sha(binding["invitation"])
            and report_sha256 == _sha(report)
        ),
        "idempotency_rate": float(
            idempotency["first_feedback_id"] == idempotency["replay_feedback_id"]
            and idempotency["idempotency_error"] == "feedback_idempotency_conflict"
            and idempotency["phase_error"] == "feedback_phase_conflict"
        ),
        "schema_validation_rate": float(
            idempotency["phase_value_error"] == "invalid_feedback"
            and idempotency["reason_required_error"] == "invalid_feedback"
            and idempotency["free_text_error"] == "invalid_feedback"
        ),
        "expiry_fail_closed_rate": float(
            expiry["expiry_error"] == "feedback_expired"
            and expiry["expired_report_count"] == 0
        ),
        "append_only_rate": float(
            expiry["invitation_append_only"] is True
            and expiry["report_append_only"] is True
        ),
        "privacy_minimization_rate": float(
            binding["raw_capability_persisted"] is False
            and binding["free_text_columns_present"] is False
        ),
        "minimum_sample_gate_rate": float(
            sample["before"]["decision_acceptance_rate"] == 0.6
            and sample["before"]["outcome_completion_rate"] is None
            and sample["after"]["outcome_completion_rate"] == 0.8
        ),
    }


def evaluate_outcomes() -> dict:
    with TemporaryDirectory(prefix="bj-pal-outcomes-eval-") as directory:
        base = Path(directory)
        cases = [
            _binding_case(base / "binding.db"),
            _idempotency_case(base / "idempotency.db"),
            _expiry_append_only_case(base / "expiry.db"),
            _minimum_sample_case(base / "sample.db"),
        ]
    artifact = {
        "schema_version": 1,
        "classification": "synthetic_contract",
        "policy": {
            "classification": FEEDBACK_CLASSIFICATION,
            "minimum_phase_samples": MINIMUM_PHASE_SAMPLES,
            "reason_codes": sorted(REASON_CODES),
            "raw_capability_persisted": False,
            "free_text_accepted": False,
            "report_uniqueness": "plan_artifact_phase",
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
