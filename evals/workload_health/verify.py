"""Independently recompute workload aggregates, quantiles, and hashes."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


EXPECTED_CASES = {"mixed-terminal-and-active-window", "empty-window"}
STATUSES = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "dead_lettered",
    "cancelled",
    "timed_out",
)
ACTIVE = {"queued", "running"}
TERMINAL = {"succeeded", "failed", "dead_lettered", "cancelled", "timed_out"}
FAILURE = {"failed", "dead_lettered", "timed_out"}
EVENT_TYPES = {
    "submitted",
    "claimed",
    "heartbeat",
    "retry_scheduled",
    "lease_reclaimed",
    "cancel_requested",
    "cancelled",
    "replay_requested",
    "timed_out",
    "succeeded",
    "failed",
    "dead_lettered",
}
FORBIDDEN_KEYS = {
    "tenant_id",
    "submitted_by",
    "request_id",
    "request_payload",
    "job_id",
    "worker_id",
    "payload",
    "error_code",
    "error_message",
}


def _sha(payload: Any, *, compact: bool = True) -> str:
    options: dict[str, Any] = {"ensure_ascii": False, "sort_keys": True}
    if compact:
        options["separators"] = (",", ":")
    return hashlib.sha256(json.dumps(payload, **options).encode("utf-8")).hexdigest()


def canonical_artifact_sha256(payload: dict[str, Any]) -> str:
    unsigned = deepcopy(payload)
    unsigned.pop("artifact_sha256", None)
    return _sha(unsigned, compact=False)


def verify_workload_health_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported workload health artifact schema")
    if artifact.get("artifact_sha256") != canonical_artifact_sha256(artifact):
        raise ValueError("workload health artifact SHA-256 mismatch")
    result = artifact.get("result") or {}
    cases = result.get("raw_cases") or []
    if result.get("case_count") != 2 or len(cases) != 2:
        raise ValueError("workload health case count mismatch")
    if {case.get("case_id") for case in cases} != EXPECTED_CASES:
        raise ValueError("workload health case registry mismatch")
    for case in cases:
        _verify_case(case)
    metrics = _metrics(cases)
    if result.get("metrics") != metrics:
        raise ValueError("workload health metrics do not match raw cases")
    return artifact


def _verify_case(case: dict[str, Any]) -> None:
    case_id = str(case["case_id"])
    raw = case.get("input") or {}
    observed = case.get("observed") or {}
    records = raw.get("records") or []
    start = _timestamp(str(raw.get("window_start")))
    end = _timestamp(str(raw.get("window_end")))
    if end <= start or (end - start).total_seconds() > 31 * 86400:
        raise ValueError(f"workload health raw window invalid: {case_id}")
    if observed.get("version") != "durable_workload_health_v1":
        raise ValueError(f"workload health version mismatch: {case_id}")
    _verify_privacy(observed, case_id)

    status_counts = {status: 0 for status in STATUSES}
    queue: list[float] = []
    run: list[float] = []
    terminal_latency: list[float] = []
    retry_jobs = 0
    lease_jobs = 0
    projections = []
    previous_key = None
    event_count = 0
    for record in records:
        created_at = _timestamp(str(record["created_at"]))
        key = (created_at, str(record["job_id"]))
        if previous_key is not None and key <= previous_key:
            raise ValueError(f"workload health record order mismatch: {case_id}")
        if not start <= created_at < end or record.get("status") not in status_counts:
            raise ValueError(f"workload health record boundary mismatch: {case_id}")
        projection, timings, event_types = _record_projection(
            record,
            case_id,
            window_end=end,
        )
        projections.append(projection)
        status_counts[record["status"]] += 1
        event_count += len(record["events"])
        if timings[0] is not None:
            queue.append(timings[0])
        if timings[1] is not None:
            run.append(timings[1])
        if timings[2] is not None:
            terminal_latency.append(timings[2])
        retry_jobs += int("retry_scheduled" in event_types)
        lease_jobs += int("lease_reclaimed" in event_types)
        previous_key = key

    count = len(records)
    terminal_count = sum(status_counts[item] for item in TERMINAL)
    expected = {
        "window_start": _canonical_time(start),
        "window_end": _canonical_time(end),
        "window_duration_seconds": int((end - start).total_seconds()),
        "job_count": count,
        "terminal_job_count": terminal_count,
        "active_job_count": sum(status_counts[item] for item in ACTIVE),
        "status_counts": status_counts,
        "event_count": event_count,
        "retry_job_count": retry_jobs,
        "lease_recovery_job_count": lease_jobs,
        "terminal_success_rate": _rate(status_counts["succeeded"], terminal_count),
        "terminal_failure_rate": _rate(
            sum(status_counts[item] for item in FAILURE), terminal_count
        ),
        "dead_letter_rate": _rate(status_counts["dead_lettered"], terminal_count),
        "timeout_rate": _rate(status_counts["timed_out"], terminal_count),
        "cancellation_rate": _rate(status_counts["cancelled"], terminal_count),
        "retry_job_rate": _rate(retry_jobs, count),
        "lease_recovery_job_rate": _rate(lease_jobs, count),
        "queue_wait_ms": _distribution(queue),
        "run_duration_ms": _distribution(run),
        "time_to_terminal_ms": _distribution(terminal_latency),
        "evidence_sha256": _sha(projections),
    }
    for field, value in expected.items():
        if observed.get(field) != value:
            raise ValueError(f"workload health {field} mismatch: {case_id}")
    unsigned = dict(observed)
    recorded_sha = unsigned.pop("artifact_sha256", None)
    if recorded_sha != _sha(unsigned):
        raise ValueError(f"workload health inner artifact SHA mismatch: {case_id}")
    labels = case.get("expected") or {}
    if not _expected_labels_match(labels, observed):
        raise ValueError(f"workload health expected labels mismatch: {case_id}")


def _record_projection(
    record: dict[str, Any],
    case_id: str,
    *,
    window_end: datetime,
) -> tuple[dict[str, Any], tuple[float | None, ...], set[str]]:
    events = record.get("events") or []
    if not events or events[0].get("event_type") != "submitted":
        raise ValueError(f"workload health submitted event missing: {case_id}")
    event_types = {str(event.get("event_type")) for event in events}
    if not event_types <= EVENT_TYPES:
        raise ValueError(f"workload health event type invalid: {case_id}")
    previous_id = 0
    previous_time = None
    projection_events = []
    derived_status = "queued"
    for event in events:
        created_at = _timestamp(str(event["created_at"]))
        if created_at >= window_end:
            raise ValueError(f"workload event outside as-of window: {case_id}")
        event_id = int(event["event_id"])
        if event_id <= previous_id or (
            previous_time is not None and created_at < previous_time
        ):
            raise ValueError(f"workload health event order mismatch: {case_id}")
        projection_events.append(
            {
                "event_id": event_id,
                "event_type": event["event_type"],
                "attempt": int(event["attempt"]),
                "created_at": _canonical_time(created_at),
            }
        )
        previous_id = event_id
        previous_time = created_at
        if event["event_type"] in {"claimed", "lease_reclaimed"}:
            derived_status = "running"
        elif event["event_type"] == "retry_scheduled":
            derived_status = "queued"
        elif event["event_type"] in TERMINAL:
            derived_status = str(event["event_type"])
    status = record["status"]
    terminal_types = event_types.intersection(TERMINAL)
    if status in ACTIVE and terminal_types:
        raise ValueError(f"workload health active terminal conflict: {case_id}")
    if status in TERMINAL and terminal_types != {status}:
        raise ValueError(f"workload health terminal mismatch: {case_id}")
    if derived_status != status:
        raise ValueError(f"workload health as-of status mismatch: {case_id}")
    submitted = _timestamp(str(events[0]["created_at"]))
    claimed = next(
        (_timestamp(str(item["created_at"])) for item in events if item["event_type"] == "claimed"),
        None,
    )
    terminal = next(
        (
            _timestamp(str(item["created_at"]))
            for item in reversed(events)
            if item["event_type"] == status and status in TERMINAL
        ),
        None,
    )
    return (
        {
            "job_id_sha256": hashlib.sha256(
                str(record["job_id"]).encode("utf-8")
            ).hexdigest(),
            "status": status,
            "created_at": _canonical_time(_timestamp(str(record["created_at"]))),
            "events": projection_events,
        },
        (_elapsed(submitted, claimed), _elapsed(claimed, terminal), _elapsed(submitted, terminal)),
        event_types,
    )


def _expected_labels_match(labels: dict[str, Any], observed: dict[str, Any]) -> bool:
    mapping = {
        "job_count": observed["job_count"],
        "terminal_job_count": observed["terminal_job_count"],
        "active_job_count": observed["active_job_count"],
        "terminal_success_rate": observed["terminal_success_rate"],
        "terminal_failure_rate": observed["terminal_failure_rate"],
        "dead_letter_rate": observed["dead_letter_rate"],
        "timeout_rate": observed["timeout_rate"],
        "cancellation_rate": observed["cancellation_rate"],
        "retry_job_rate": observed["retry_job_rate"],
        "queue_p50_ms": observed["queue_wait_ms"]["p50_ms"],
        "queue_p95_ms": observed["queue_wait_ms"]["p95_ms"],
        "run_p50_ms": observed["run_duration_ms"]["p50_ms"],
        "terminal_p95_ms": observed["time_to_terminal_ms"]["p95_ms"],
    }
    return mapping == labels


def _verify_privacy(value: Any, case_id: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False).lower()
    if "synthetic-job" in serialized or "private" in serialized:
        raise ValueError(f"workload health private marker leaked: {case_id}")
    _verify_forbidden_keys(value, case_id)


def _verify_forbidden_keys(value: Any, case_id: str) -> None:
    if isinstance(value, dict):
        if FORBIDDEN_KEYS.intersection(value):
            raise ValueError(f"workload health forbidden key leaked: {case_id}")
        for item in value.values():
            _verify_forbidden_keys(item, case_id)
    elif isinstance(value, list):
        for item in value:
            _verify_forbidden_keys(item, case_id)


def _metrics(cases: list[dict[str, Any]]) -> dict[str, float | int]:
    return {
        "aggregate_accuracy_rate": 1.0,
        "latency_accuracy_rate": 1.0,
        "integrity_rate": 1.0,
        "privacy_minimization_rate": 1.0,
        "empty_window_null_rate": sum(
            case["case_id"] == "empty-window"
            and case["observed"]["terminal_success_rate"] is None
            and case["observed"]["retry_job_rate"] is None
            and case["observed"]["queue_wait_ms"]["sample_count"] == 0
            for case in cases
        ),
    }


def _timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("workload health timestamp timezone missing")
    return parsed


def _canonical_time(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _elapsed(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() * 1000, 3)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {
            "sample_count": 0,
            "minimum_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "maximum_ms": None,
            "quantile_method": "nearest_rank",
        }
    return {
        "sample_count": len(ordered),
        "minimum_ms": ordered[0],
        "p50_ms": _nearest(ordered, 0.50),
        "p95_ms": _nearest(ordered, 0.95),
        "p99_ms": _nearest(ordered, 0.99),
        "maximum_ms": ordered[-1],
        "quantile_method": "nearest_rank",
    }


def _nearest(values: Sequence[float], quantile: float) -> float:
    return values[max(0, math.ceil(quantile * len(values)) - 1)]
