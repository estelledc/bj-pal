"""Exercise submit, claim, execution, persistence, and artifact retrieval."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ["BJ_PAL_LLM"] = "mock"

from agents.types import UserPreferences  # noqa: E402
from application import PlanRequest  # noqa: E402
from jobs import PlanningJobRepository, PlanningJobService  # noqa: E402


class FailOncePlanningService:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls = 0

    def execute(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("synthetic transient failure")
        return self.delegate.execute(request, **kwargs)


class AlwaysFailPlanningService:
    def execute(self, request, **kwargs):
        del request, kwargs
        raise RuntimeError("synthetic terminal failure")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bj-pal-job-smoke-") as directory:
        repository = PlanningJobRepository(Path(directory) / "jobs.db")
        service = PlanningJobService(repository=repository)
        request = PlanRequest(
            user_input="周末下午带 5 岁孩子在五道营附近玩四小时，不吃辣",
            preferences=UserPreferences(
                persona="family",
                party_size=3,
                has_child=True,
                child_age=5,
                diet_flags=["no_spicy"],
                duration_hours=4,
                raw_input="周末下午带 5 岁孩子在五道营附近玩四小时，不吃辣",
            ),
        )
        submitted = service.submit(
            request=request,
            request_id="smoke-job-request",
            idempotency_key="smoke-job-v1",
            priority=7,
        )
        completed = service.run_once(worker_id="smoke-worker")
        persisted = service.get(submitted.job_id)
        assert completed is not None and completed.status == "succeeded"
        assert persisted is not None and persisted.result_payload is not None
        assert persisted.artifact_sha256
        events = service.events(persisted.job_id)
        assert [event.event_type for event in events] == ["submitted", "claimed", "succeeded"]
        assert persisted.priority == 7
        assert (
            events[1].payload["scheduling_policy"]
            == "tenant_fair_priority_aging_v2"
        )
        assert events[1].payload["priority_policy"] == "priority_aging_v1"
        assert (
            events[1].payload["tenant_fairness_policy"]
            == "least_recently_served_tenant_v1"
        )
        assert events[1].payload["tenant_id"] == "default"
        assert events[1].payload["base_priority"] == 7
        assert events[1].payload["queue_wait_ms"] >= 0

        recovery_service = PlanningJobService(
            repository=PlanningJobRepository(Path(directory) / "recovery.db"),
            planning_service=FailOncePlanningService(service.planning_service),
            max_attempts=2,
            retry_base_seconds=0,
            retry_max_seconds=0,
        )
        recovery_job = recovery_service.submit(
            request=request,
            request_id="smoke-recovery-request",
            idempotency_key="smoke-recovery-v1",
        )
        retry_scheduled = recovery_service.run_once(
            worker_id="smoke-recovery-1",
            lease_seconds=1,
            heartbeat_interval_seconds=0.01,
        )
        recovered = recovery_service.run_once(
            worker_id="smoke-recovery-2",
            lease_seconds=1,
            heartbeat_interval_seconds=0.01,
        )
        recovery_events = recovery_service.events(recovery_job.job_id)
        recovery_types = [event.event_type for event in recovery_events]
        assert retry_scheduled is not None and retry_scheduled.status == "queued"
        assert recovered is not None and recovered.status == "succeeded"
        assert recovery_types[:4] == [
            "submitted",
            "claimed",
            "retry_scheduled",
            "claimed",
        ]
        assert recovery_types[-1] == "succeeded"
        assert "heartbeat" in recovery_types

        control_service = PlanningJobService(
            repository=PlanningJobRepository(Path(directory) / "control.db"),
            planning_service=AlwaysFailPlanningService(),
            max_attempts=1,
            retry_base_seconds=0,
        )
        failed_source = control_service.submit(
            request=request,
            request_id="smoke-dead-letter-request",
            idempotency_key="smoke-dead-letter-v1",
            priority=6,
        )
        dead_lettered = control_service.run_once(worker_id="smoke-dead-letter-worker")
        assert dead_lettered is not None and dead_lettered.status == "dead_lettered"
        assert [job.job_id for job in control_service.list_jobs(status="dead_lettered")] == [
            failed_source.job_id
        ]
        replayed = control_service.replay(
            job_id=failed_source.job_id,
            request_id="smoke-replay-request",
            idempotency_key="smoke-replay-v1",
        )
        cancelled = control_service.cancel(
            job_id=replayed.job_id,
            reason_code="operator_requested",
        )
        assert cancelled.status == "cancelled"
        assert replayed.priority == failed_source.priority == 6
        assert replayed.replayed_from_job_id == failed_source.job_id
        assert control_service.events(failed_source.job_id)[-1].event_type == "replay_requested"

        deadline_repository = PlanningJobRepository(Path(directory) / "deadline.db")
        deadline_service = PlanningJobService(
            repository=deadline_repository,
            planning_service=service.planning_service,
        )
        expiring = deadline_service.submit(
            request=request,
            request_id="smoke-timeout-request",
            idempotency_key="smoke-timeout-v1",
            priority=4,
            deadline_seconds=30,
        )
        with deadline_repository._connect() as connection:
            connection.execute(
                "UPDATE planning_jobs SET deadline_at = ? WHERE job_id = ?",
                ("2000-01-01T00:00:00.000Z", expiring.job_id),
            )
        assert deadline_service.run_once(worker_id="smoke-timeout-worker") is None
        timed_out = deadline_service.get(expiring.job_id)
        assert timed_out is not None and timed_out.status == "timed_out"
        timeout_replay = deadline_service.replay(
            job_id=timed_out.job_id,
            request_id="smoke-timeout-replay-request",
            idempotency_key="smoke-timeout-replay-v1",
        )
        assert timeout_replay.deadline_seconds == 30
        assert timeout_replay.priority == timed_out.priority == 4
        assert timeout_replay.deadline_at != timed_out.deadline_at
        print(
            "job smoke: "
            f"job={persisted.job_id} status={persisted.status} attempt={persisted.attempt} "
            f"priority={persisted.priority} queue_wait_ms={events[1].payload['queue_wait_ms']} "
            f"artifact={persisted.artifact_id} steps="
            f"{len(persisted.result_payload['final_plan']['steps'])} events={len(events)} "
            f"recovery_attempt={recovered.attempt} "
            f"heartbeats={recovery_types.count('heartbeat')} "
            f"dead_letter={dead_lettered.status} replay={replayed.status} "
            f"cancel={cancelled.status} timeout={timed_out.status} "
            f"timeout_replay={timeout_replay.status}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
