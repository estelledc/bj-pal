"""Rehearse the full trial lifecycle without creating real-user evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from outcomes import PlanFeedbackRepository  # noqa: E402


def rehearse(repository: PlanFeedbackRepository, *, participant_count: int = 5) -> dict:
    if participant_count < 5:
        raise ValueError("trial rehearsal requires at least five synthetic participants")
    trial = repository.create_trial(
        created_by="rehearsal-operator",
        tenant_id="rehearsal",
        duration_days=7,
        retention_days=30,
        minimum_participants=5,
    )
    for index in range(participant_count):
        enrollment = repository.issue_trial_enrollment(
            trial_id=trial.trial_id,
            tenant_id=trial.tenant_id,
            issued_by="rehearsal-operator",
        )
        participant = repository.enroll_trial(
            trial_id=trial.trial_id,
            enrollment_capability=enrollment.capability,
            consent_notice_sha256=trial.consent_notice_sha256,
            consent_attested=True,
        )
        invitation = repository.issue(
            plan_id=f"rehearsal-plan-{index}",
            plan_artifact_sha256=f"{index + 1:064x}",
            data_profile_name="demo",
            data_profile_classification="synthetic",
            trial_participant_capability=participant.capability,
        )
        repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key=f"rehearsal-decision-{index}",
            phase="decision",
            value="accepted" if index < participant_count - 1 else "rejected",
            reason_codes=() if index < participant_count - 1 else ("too_far",),
        )
        repository.submit(
            plan_id=invitation.plan_id,
            capability=invitation.capability,
            idempotency_key=f"rehearsal-outcome-{index}",
            phase="outcome",
            value="completed" if index < participant_count - 1 else "abandoned",
            reason_codes=(
                () if index < participant_count - 1 else ("weather_issue",)
            ),
        )
    snapshot = repository.close_trial(
        trial_id=trial.trial_id,
        tenant_id=trial.tenant_id,
        closed_by="rehearsal-operator",
    ).to_dict()
    return {
        "version": "trial_rehearsal_result_v1",
        "classification": "synthetic_rehearsal_not_human_evidence",
        "trial_id": trial.trial_id,
        "consent_notice_sha256": trial.consent_notice_sha256,
        "participant_capability_count": participant_count,
        "phase_participant_counts": snapshot["phase_participant_counts"],
        "decision_acceptance_rate": snapshot["decision_acceptance_rate"],
        "outcome_completion_rate": snapshot["outcome_completion_rate"],
        "evidence_root_sha256": snapshot["evidence_root_sha256"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "raw_capabilities_printed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--participants", type=int, default=5)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Optional explicit SQLite path; default rehearsal uses a temporary database.",
    )
    args = parser.parse_args()
    if args.db is not None:
        result = rehearse(
            PlanFeedbackRepository(args.db),
            participant_count=args.participants,
        )
    else:
        with TemporaryDirectory(prefix="bj-pal-trial-rehearsal-") as directory:
            result = rehearse(
                PlanFeedbackRepository(Path(directory) / "feedback.db"),
                participant_count=args.participants,
            )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
