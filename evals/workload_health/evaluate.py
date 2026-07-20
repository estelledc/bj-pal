"""Build fixed synthetic windows through the product workload aggregator."""

from __future__ import annotations

import json
from typing import Any

from jobs import (
    DurableWorkloadHealth,
    PlanningJobEvent,
    PlanningJobWindowEvidence,
)


WINDOW_START = "2026-07-20T00:00:00.000Z"
WINDOW_END = "2026-07-21T00:00:00.000Z"


RAW_CASES = (
    {
        "case_id": "mixed-terminal-and-active-window",
        "records": (
            {
                "job_id": "synthetic-job-a",
                "status": "succeeded",
                "created_at": "2026-07-20T00:00:00.000Z",
                "events": (("submitted", 0), ("claimed", 1), ("succeeded", 5)),
            },
            {
                "job_id": "synthetic-job-b",
                "status": "dead_lettered",
                "created_at": "2026-07-20T00:00:10.000Z",
                "events": (
                    ("submitted", 10),
                    ("claimed", 12),
                    ("retry_scheduled", 14),
                    ("claimed", 15),
                    ("dead_lettered", 18),
                ),
            },
            {
                "job_id": "synthetic-job-c",
                "status": "timed_out",
                "created_at": "2026-07-20T00:00:20.000Z",
                "events": (("submitted", 20), ("timed_out", 27)),
            },
            {
                "job_id": "synthetic-job-d",
                "status": "timed_out",
                "created_at": "2026-07-20T00:00:30.000Z",
                "events": (("submitted", 30), ("claimed", 34), ("timed_out", 40)),
            },
            {
                "job_id": "synthetic-job-e",
                "status": "cancelled",
                "created_at": "2026-07-20T00:00:50.000Z",
                "events": (("submitted", 50), ("cancelled", 52)),
            },
            {
                "job_id": "synthetic-job-f",
                "status": "queued",
                "created_at": "2026-07-20T00:00:59.000Z",
                "events": (("submitted", 59),),
            },
        ),
        "expected": {
            "job_count": 6,
            "terminal_job_count": 5,
            "active_job_count": 1,
            "terminal_success_rate": 0.2,
            "terminal_failure_rate": 0.6,
            "dead_letter_rate": 0.2,
            "timeout_rate": 0.4,
            "cancellation_rate": 0.2,
            "retry_job_rate": 0.166667,
            "queue_p50_ms": 2000.0,
            "queue_p95_ms": 4000.0,
            "run_p50_ms": 6000.0,
            "terminal_p95_ms": 10000.0,
        },
    },
    {
        "case_id": "empty-window",
        "records": (),
        "expected": {
            "job_count": 0,
            "terminal_job_count": 0,
            "active_job_count": 0,
            "terminal_success_rate": None,
            "terminal_failure_rate": None,
            "dead_letter_rate": None,
            "timeout_rate": None,
            "cancellation_rate": None,
            "retry_job_rate": None,
            "queue_p50_ms": None,
            "queue_p95_ms": None,
            "run_p50_ms": None,
            "terminal_p95_ms": None,
        },
    },
)


def _record(raw: dict[str, Any]) -> PlanningJobWindowEvidence:
    events = tuple(
        PlanningJobEvent(
            event_id=index,
            job_id=raw["job_id"],
            event_type=event_type,
            attempt=0 if event_type == "submitted" else 1,
            worker_id="private-synthetic-worker",
            payload={"private": "private-synthetic-payload"},
            created_at=f"2026-07-20T00:00:{second:02d}.000Z",
        )
        for index, (event_type, second) in enumerate(raw["events"], start=1)
    )
    return PlanningJobWindowEvidence(
        job_id=raw["job_id"],
        status=raw["status"],
        created_at=raw["created_at"],
        events=events,
    )


def _raw_projection(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": raw["job_id"],
        "status": raw["status"],
        "created_at": raw["created_at"],
        "events": [
            {
                "event_id": index,
                "event_type": event_type,
                "attempt": 0 if event_type == "submitted" else 1,
                "created_at": f"2026-07-20T00:00:{second:02d}.000Z",
            }
            for index, (event_type, second) in enumerate(raw["events"], start=1)
        ],
    }


def evaluate_workload_health() -> dict[str, Any]:
    cases = []
    for fixture in RAW_CASES:
        snapshot = DurableWorkloadHealth.create(
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            records=tuple(_record(item) for item in fixture["records"]),
        )
        cases.append(
            {
                "case_id": fixture["case_id"],
                "input": {
                    "window_start": WINDOW_START,
                    "window_end": WINDOW_END,
                    "records": [
                        _raw_projection(item) for item in fixture["records"]
                    ],
                },
                "expected": dict(fixture["expected"]),
                "observed": snapshot.to_dict(),
            }
        )
    return {
        "case_count": len(cases),
        "raw_cases": cases,
        "metrics": recompute_metrics(cases),
    }


def recompute_metrics(cases: list[dict[str, Any]]) -> dict[str, float | int]:
    if not cases:
        raise ValueError("workload health metrics require cases")
    aggregate_passes = 0
    latency_passes = 0
    integrity_passes = 0
    privacy_passes = 0
    empty_null_passes = 0
    for case in cases:
        observed = case["observed"]
        expected = case["expected"]
        aggregate_passes += int(
            all(
                observed[key] == expected[key]
                for key in (
                    "job_count",
                    "terminal_job_count",
                    "active_job_count",
                    "terminal_success_rate",
                    "terminal_failure_rate",
                    "dead_letter_rate",
                    "timeout_rate",
                    "cancellation_rate",
                    "retry_job_rate",
                )
            )
        )
        latency_passes += int(
            observed["queue_wait_ms"]["p50_ms"] == expected["queue_p50_ms"]
            and observed["queue_wait_ms"]["p95_ms"] == expected["queue_p95_ms"]
            and observed["run_duration_ms"]["p50_ms"] == expected["run_p50_ms"]
            and observed["time_to_terminal_ms"]["p95_ms"]
            == expected["terminal_p95_ms"]
        )
        integrity_passes += int(
            DurableWorkloadHealth.from_dict(observed).verify_integrity()
        )
        serialized = json.dumps(observed, ensure_ascii=False).lower()
        privacy_passes += int(
            "synthetic-job" not in serialized and "private" not in serialized
        )
        if case["case_id"] == "empty-window":
            empty_null_passes += int(
                observed["terminal_success_rate"] is None
                and observed["retry_job_rate"] is None
                and observed["queue_wait_ms"]["sample_count"] == 0
            )
    count = len(cases)
    return {
        "aggregate_accuracy_rate": aggregate_passes / count,
        "latency_accuracy_rate": latency_passes / count,
        "integrity_rate": integrity_passes / count,
        "privacy_minimization_rate": privacy_passes / count,
        "empty_window_null_rate": empty_null_passes,
    }
