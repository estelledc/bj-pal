"""Use-case facade for capability-bound plan feedback."""

from __future__ import annotations

from datetime import datetime

from .models import FeedbackInvitation, PlanFeedbackReport
from .repository import MINIMUM_PHASE_SAMPLES, PlanFeedbackRepository


class PlanFeedbackService:
    def __init__(self, repository: PlanFeedbackRepository | None = None) -> None:
        self.repository = repository or PlanFeedbackRepository()

    def issue(self, **kwargs) -> FeedbackInvitation:
        return self.repository.issue(**kwargs)

    def submit(self, **kwargs) -> PlanFeedbackReport:
        return self.repository.submit(**kwargs)

    def list_reports(
        self,
        *,
        plan_id: str,
        capability: str,
        now: datetime | None = None,
    ) -> tuple[PlanFeedbackReport, ...]:
        return self.repository.list_reports(
            plan_id=plan_id,
            capability=capability,
            now=now,
        )

    def public_summary(self, *, minimum_phase_samples: int = MINIMUM_PHASE_SAMPLES) -> dict:
        return self.repository.public_summary(
            minimum_phase_samples=minimum_phase_samples
        )

    def create_trial(self, **kwargs):
        return self.repository.create_trial(**kwargs)

    def get_trial(self, trial_id: str):
        return self.repository.get_trial(trial_id)

    def issue_trial_enrollment(self, **kwargs):
        return self.repository.issue_trial_enrollment(**kwargs)

    def issue_trial_enrollments(self, **kwargs):
        return self.repository.issue_trial_enrollments(**kwargs)

    def enroll_trial(self, **kwargs):
        return self.repository.enroll_trial(**kwargs)

    def authorize_trial_participant(self, **kwargs):
        return self.repository.authorize_trial_participant(**kwargs)

    def withdraw_trial(self, **kwargs):
        return self.repository.withdraw_trial(**kwargs)

    def trial_summary(self, **kwargs) -> dict:
        return self.repository.trial_summary(**kwargs)

    def close_trial(self, **kwargs):
        return self.repository.close_trial(**kwargs)

    def get_trial_purge_receipt(self, trial_id: str, **kwargs):
        return self.repository.get_trial_purge_receipt(trial_id, **kwargs)

    def purge_trial(self, **kwargs):
        return self.repository.purge_trial(**kwargs)
