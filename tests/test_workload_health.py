from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs import (  # noqa: E402
    DurableWorkloadHealth,
    JobWorkloadEvidenceLimitExceeded,
    PlanningJobEvent,
    PlanningJobRepository,
    PlanningJobService,
    PlanningJobWindowEvidence,
)


WINDOW_START = "2026-07-20T00:00:00.000Z"
WINDOW_END = "2026-07-21T00:00:00.000Z"


def _event(
    job_id: str,
    event_id: int,
    event_type: str,
    second: int,
    *,
    attempt: int = 1,
) -> PlanningJobEvent:
    return PlanningJobEvent(
        event_id=event_id,
        job_id=job_id,
        event_type=event_type,
        attempt=attempt,
        worker_id="private-worker-marker",
        payload={"private": "private-event-marker"},
        created_at=f"2026-07-20T00:00:{second:02d}.000Z",
    )


def _record(
    suffix: str,
    status: str,
    created_second: int,
    events: tuple[tuple[str, int], ...],
) -> PlanningJobWindowEvidence:
    job_id = f"job-private-{suffix}"
    return PlanningJobWindowEvidence(
        job_id=job_id,
        status=status,
        created_at=f"2026-07-20T00:00:{created_second:02d}.000Z",
        events=tuple(
            _event(
                job_id,
                index,
                event_type,
                second,
                attempt=0 if event_type == "submitted" else 1,
            )
            for index, (event_type, second) in enumerate(events, start=1)
        ),
    )


def _mixed_records() -> tuple[PlanningJobWindowEvidence, ...]:
    return (
        _record(
            "a",
            "succeeded",
            0,
            (("submitted", 0), ("claimed", 1), ("succeeded", 5)),
        ),
        _record(
            "b",
            "dead_lettered",
            10,
            (
                ("submitted", 10),
                ("claimed", 12),
                ("retry_scheduled", 14),
                ("claimed", 15),
                ("dead_lettered", 18),
            ),
        ),
        _record(
            "c",
            "timed_out",
            20,
            (("submitted", 20), ("timed_out", 27)),
        ),
        _record(
            "d",
            "timed_out",
            30,
            (("submitted", 30), ("claimed", 34), ("timed_out", 40)),
        ),
        _record(
            "e",
            "cancelled",
            50,
            (("submitted", 50), ("cancelled", 52)),
        ),
        _record("f", "queued", 59, (("submitted", 59),)),
    )


def test_mixed_window_has_explicit_denominators_and_nearest_rank_latency() -> None:
    snapshot = DurableWorkloadHealth.create(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        records=_mixed_records(),
    )
    serialized = json.dumps(snapshot.to_dict(), ensure_ascii=False)

    assert snapshot.job_count == 6
    assert snapshot.terminal_job_count == 5
    assert snapshot.active_job_count == 1
    assert snapshot.status_counts == {
        "queued": 1,
        "running": 0,
        "succeeded": 1,
        "failed": 0,
        "dead_lettered": 1,
        "cancelled": 1,
        "timed_out": 2,
    }
    assert snapshot.event_count == 16
    assert snapshot.retry_job_count == 1
    assert snapshot.terminal_success_rate == 0.2
    assert snapshot.terminal_failure_rate == 0.6
    assert snapshot.dead_letter_rate == 0.2
    assert snapshot.timeout_rate == 0.4
    assert snapshot.cancellation_rate == 0.2
    assert snapshot.retry_job_rate == 0.166667
    assert snapshot.queue_wait_ms.to_dict() == {
        "sample_count": 3,
        "minimum_ms": 1000.0,
        "p50_ms": 2000.0,
        "p95_ms": 4000.0,
        "p99_ms": 4000.0,
        "maximum_ms": 4000.0,
        "quantile_method": "nearest_rank",
    }
    assert snapshot.run_duration_ms.p50_ms == 6000.0
    assert snapshot.time_to_terminal_ms.p50_ms == 7000.0
    assert snapshot.time_to_terminal_ms.p95_ms == 10000.0
    assert snapshot.verify_integrity()
    assert "private" not in serialized


def test_empty_window_uses_null_rates_and_zero_sample_distributions() -> None:
    snapshot = DurableWorkloadHealth.create(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        records=(),
    )

    assert snapshot.job_count == 0
    assert snapshot.terminal_success_rate is None
    assert snapshot.retry_job_rate is None
    assert snapshot.queue_wait_ms.sample_count == 0
    assert snapshot.queue_wait_ms.p50_ms is None
    assert snapshot.verify_integrity()


def test_integrity_window_and_event_validation_fail_closed() -> None:
    records = _mixed_records()
    snapshot = DurableWorkloadHealth.create(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        records=records,
    )
    tampered = snapshot.to_dict()
    tampered["terminal_success_rate"] = 1.0

    with pytest.raises(ValueError, match="integrity"):
        DurableWorkloadHealth.from_dict(tampered)
    with pytest.raises(ValueError, match="31 days"):
        DurableWorkloadHealth.create(
            window_start=WINDOW_START,
            window_end="2026-09-01T00:00:00.000Z",
            records=(),
        )
    with pytest.raises(ValueError, match="strictly ordered"):
        DurableWorkloadHealth.create(
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            records=tuple(reversed(records)),
        )
    missing_terminal = replace(records[0], events=records[0].events[:-1])
    with pytest.raises(ValueError, match="matching event"):
        DurableWorkloadHealth.create(
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            records=(missing_terminal,),
        )
    invalid_event = replace(
        records[0],
        events=(
            records[0].events[0],
            replace(records[0].events[1], event_type="private-event"),
            records[0].events[2],
        ),
    )
    with pytest.raises(ValueError, match="event type"):
        DurableWorkloadHealth.create(
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            records=(invalid_event,),
        )
    active_with_terminal = replace(
        records[-1],
        created_at=records[0].created_at,
        events=records[0].events,
    )
    active_with_terminal = replace(
        active_with_terminal,
        events=tuple(
            replace(event, job_id=active_with_terminal.job_id)
            for event in active_with_terminal.events
        ),
    )
    with pytest.raises(ValueError, match="active.*terminal"):
        DurableWorkloadHealth.create(
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            records=(active_with_terminal,),
        )
    queued_with_claim = replace(
        records[-1],
        events=(
            records[-1].events[0],
            _event(records[-1].job_id, 2, "claimed", 59),
        ),
    )
    with pytest.raises(ValueError, match="as-of event prefix"):
        DurableWorkloadHealth.create(
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            records=(queued_with_claim,),
        )
    event_after_window = replace(
        records[0],
        events=records[0].events
        + (
            replace(
                records[0].events[-1],
                event_id=4,
                event_type="replay_requested",
                created_at=WINDOW_END,
            ),
        ),
    )
    with pytest.raises(ValueError, match="as-of window"):
        DurableWorkloadHealth.create(
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            records=(event_after_window,),
        )


def test_repository_and_service_apply_tenant_and_window_boundaries(tmp_path: Path) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    alpha = repository.submit(
        request_id="workload-alpha",
        request_payload={"user_input": "private-alpha"},
        tenant_id="tenant-alpha",
        submitted_by="workload-test",
    )
    repository.submit(
        request_id="workload-beta",
        request_payload={"user_input": "private-beta"},
        tenant_id="tenant-beta",
        submitted_by="workload-test",
    )
    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=1)).isoformat()
    end = datetime.now(timezone.utc).isoformat()

    snapshot = PlanningJobService(repository=repository).workload_health(
        tenant_id="tenant-alpha",
        window_start=start,
        window_end=end,
    )
    empty = PlanningJobService(repository=repository).workload_health(
        tenant_id="tenant-alpha",
        window_start="2025-01-01T00:00:00Z",
        window_end="2025-01-02T00:00:00Z",
    )

    assert snapshot.job_count == 1
    assert snapshot.status_counts["queued"] == 1
    assert snapshot.event_count == 1
    assert empty.job_count == 0
    assert alpha.job_id not in json.dumps(snapshot.to_dict())
    with pytest.raises(ValueError, match="future"):
        PlanningJobService(repository=repository).workload_health(
            tenant_id="tenant-alpha",
            window_start=start,
            window_end=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
        )


def test_repository_refuses_to_truncate_job_window(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    for index in range(2):
        repository.submit(
            request_id=f"workload-{index}",
            request_payload={"user_input": "bounded"},
            tenant_id="tenant-alpha",
            submitted_by="workload-test",
        )
    now = datetime.now(timezone.utc)
    monkeypatch.setattr("jobs.repository.MAX_WORKLOAD_JOBS", 1)

    with pytest.raises(JobWorkloadEvidenceLimitExceeded, match="job limit"):
        repository.workload_evidence(
            tenant_id="tenant-alpha",
            window_start=(now - timedelta(minutes=1)).isoformat(),
            window_end=datetime.now(timezone.utc).isoformat(),
        )


def test_repository_reconstructs_stable_status_as_of_window_end(
    monkeypatch,
    tmp_path: Path,
) -> None:
    clock = {"now": datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr("jobs.repository._utc_now", lambda: clock["now"])
    repository = PlanningJobRepository(tmp_path / "jobs.db")
    job = repository.submit(
        request_id="historical-cutoff",
        request_payload={"user_input": "bounded"},
        tenant_id="tenant-alpha",
        submitted_by="workload-test",
    )
    cutoff = clock["now"] + timedelta(seconds=1)
    clock["now"] += timedelta(seconds=2)
    claimed = repository.claim_next(worker_id="worker-alpha")
    assert claimed is not None
    clock["now"] += timedelta(seconds=1)
    repository.succeed(
        job_id=job.job_id,
        worker_id="worker-alpha",
        result_payload={"artifact": "private"},
    )

    snapshot = PlanningJobService(repository=repository).workload_health(
        tenant_id="tenant-alpha",
        window_start="2026-07-19T09:59:00Z",
        window_end=cutoff.isoformat(),
    )

    assert snapshot.status_counts["queued"] == 1
    assert snapshot.status_counts["succeeded"] == 0
    assert snapshot.event_count == 1
    assert snapshot.queue_wait_ms.sample_count == 0
