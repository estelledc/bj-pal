"""Deterministic, privacy-minimized health snapshots for durable job windows."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .models import PlanningJobEvent, PlanningJobWindowEvidence


WORKLOAD_HEALTH_VERSION = "durable_workload_health_v1"
MAX_WORKLOAD_JOBS = 1000
MAX_WORKLOAD_EVENTS = 10000
MAX_WORKLOAD_WINDOW = timedelta(days=31)

_STATUSES = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "dead_lettered",
    "cancelled",
    "timed_out",
)
_ACTIVE_STATUSES = {"queued", "running"}
_TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "dead_lettered",
    "cancelled",
    "timed_out",
}
_FAILURE_STATUSES = {"failed", "dead_lettered", "timed_out"}
_EVENT_TYPES = {
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


class JobWorkloadEvidenceLimitExceeded(RuntimeError):
    """The bounded reader refused to publish a partial aggregate."""


def _canonical_sha256(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def parse_utc_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("workload window timestamps must be non-empty strings")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("workload window timestamps must use ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("workload window timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def canonical_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def validate_window(window_start: str, window_end: str) -> tuple[datetime, datetime]:
    start = parse_utc_timestamp(window_start)
    end = parse_utc_timestamp(window_end)
    duration = end - start
    if duration <= timedelta(0):
        raise ValueError("workload window end must be after its start")
    if duration > MAX_WORKLOAD_WINDOW:
        raise ValueError("workload window cannot exceed 31 days")
    return start, end


def validate_closed_window(
    window_start: str,
    window_end: str,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    start, end = validate_window(window_start, window_end)
    observed_now = now or datetime.now(timezone.utc)
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        raise ValueError("workload observation time must include a timezone")
    if end > observed_now.astimezone(timezone.utc):
        raise ValueError("workload window end cannot be in the future")
    return start, end


@dataclass(frozen=True)
class LatencyDistribution:
    sample_count: int
    minimum_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    maximum_ms: float | None
    quantile_method: str = "nearest_rank"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DurableWorkloadHealth:
    version: str
    window_start: str
    window_end: str
    window_duration_seconds: int
    job_count: int
    terminal_job_count: int
    active_job_count: int
    status_counts: dict[str, int]
    event_count: int
    retry_job_count: int
    lease_recovery_job_count: int
    terminal_success_rate: float | None
    terminal_failure_rate: float | None
    dead_letter_rate: float | None
    timeout_rate: float | None
    cancellation_rate: float | None
    retry_job_rate: float | None
    lease_recovery_job_rate: float | None
    queue_wait_ms: LatencyDistribution
    run_duration_ms: LatencyDistribution
    time_to_terminal_ms: LatencyDistribution
    evidence_sha256: str
    artifact_sha256: str

    @classmethod
    def create(
        cls,
        *,
        window_start: str,
        window_end: str,
        records: Sequence[PlanningJobWindowEvidence],
    ) -> "DurableWorkloadHealth":
        start, end = validate_window(window_start, window_end)
        if len(records) > MAX_WORKLOAD_JOBS:
            raise JobWorkloadEvidenceLimitExceeded(
                "durable workload job limit exceeded; aggregate was not truncated"
            )
        if sum(len(record.events) for record in records) > MAX_WORKLOAD_EVENTS:
            raise JobWorkloadEvidenceLimitExceeded(
                "durable workload event limit exceeded; aggregate was not truncated"
            )
        projections: list[dict[str, Any]] = []
        queue_waits: list[float] = []
        run_durations: list[float] = []
        terminal_durations: list[float] = []
        status_counts = {status: 0 for status in _STATUSES}
        retry_jobs = 0
        lease_recovery_jobs = 0
        event_count = 0
        previous_key: tuple[datetime, str] | None = None

        for record in records:
            created_at = parse_utc_timestamp(record.created_at)
            key = (created_at, record.job_id)
            if previous_key is not None and key <= previous_key:
                raise ValueError("workload records must be strictly ordered")
            if not start <= created_at < end:
                raise ValueError("workload record falls outside the requested window")
            if record.status not in status_counts:
                raise ValueError("workload record status is invalid")
            projection, timings = _project_record(record, window_end=end)
            projections.append(projection)
            status_counts[record.status] += 1
            event_count += len(record.events)
            if timings["queue_wait_ms"] is not None:
                queue_waits.append(timings["queue_wait_ms"])
            if timings["run_duration_ms"] is not None:
                run_durations.append(timings["run_duration_ms"])
            if timings["time_to_terminal_ms"] is not None:
                terminal_durations.append(timings["time_to_terminal_ms"])
            event_types = {event.event_type for event in record.events}
            retry_jobs += int("retry_scheduled" in event_types)
            lease_recovery_jobs += int("lease_reclaimed" in event_types)
            previous_key = key

        job_count = len(records)
        terminal_count = sum(status_counts[item] for item in _TERMINAL_STATUSES)
        active_count = sum(status_counts[item] for item in _ACTIVE_STATUSES)
        failure_count = sum(status_counts[item] for item in _FAILURE_STATUSES)
        payload = {
            "version": WORKLOAD_HEALTH_VERSION,
            "window_start": canonical_timestamp(start),
            "window_end": canonical_timestamp(end),
            "window_duration_seconds": int((end - start).total_seconds()),
            "job_count": job_count,
            "terminal_job_count": terminal_count,
            "active_job_count": active_count,
            "status_counts": status_counts,
            "event_count": event_count,
            "retry_job_count": retry_jobs,
            "lease_recovery_job_count": lease_recovery_jobs,
            "terminal_success_rate": _rate(status_counts["succeeded"], terminal_count),
            "terminal_failure_rate": _rate(failure_count, terminal_count),
            "dead_letter_rate": _rate(status_counts["dead_lettered"], terminal_count),
            "timeout_rate": _rate(status_counts["timed_out"], terminal_count),
            "cancellation_rate": _rate(status_counts["cancelled"], terminal_count),
            "retry_job_rate": _rate(retry_jobs, job_count),
            "lease_recovery_job_rate": _rate(lease_recovery_jobs, job_count),
            "queue_wait_ms": _distribution(queue_waits).to_dict(),
            "run_duration_ms": _distribution(run_durations).to_dict(),
            "time_to_terminal_ms": _distribution(terminal_durations).to_dict(),
            "evidence_sha256": _canonical_sha256(projections),
        }
        return cls._from_payload(payload)

    @classmethod
    def _from_payload(cls, payload: Mapping[str, Any]) -> "DurableWorkloadHealth":
        unsigned = dict(payload)
        unsigned.pop("artifact_sha256", None)
        return cls(
            version=str(unsigned["version"]),
            window_start=str(unsigned["window_start"]),
            window_end=str(unsigned["window_end"]),
            window_duration_seconds=int(unsigned["window_duration_seconds"]),
            job_count=int(unsigned["job_count"]),
            terminal_job_count=int(unsigned["terminal_job_count"]),
            active_job_count=int(unsigned["active_job_count"]),
            status_counts={
                str(key): int(value)
                for key, value in dict(unsigned["status_counts"]).items()
            },
            event_count=int(unsigned["event_count"]),
            retry_job_count=int(unsigned["retry_job_count"]),
            lease_recovery_job_count=int(unsigned["lease_recovery_job_count"]),
            terminal_success_rate=unsigned["terminal_success_rate"],
            terminal_failure_rate=unsigned["terminal_failure_rate"],
            dead_letter_rate=unsigned["dead_letter_rate"],
            timeout_rate=unsigned["timeout_rate"],
            cancellation_rate=unsigned["cancellation_rate"],
            retry_job_rate=unsigned["retry_job_rate"],
            lease_recovery_job_rate=unsigned["lease_recovery_job_rate"],
            queue_wait_ms=LatencyDistribution(**unsigned["queue_wait_ms"]),
            run_duration_ms=LatencyDistribution(**unsigned["run_duration_ms"]),
            time_to_terminal_ms=LatencyDistribution(
                **unsigned["time_to_terminal_ms"]
            ),
            evidence_sha256=str(unsigned["evidence_sha256"]),
            artifact_sha256=_canonical_sha256(unsigned),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DurableWorkloadHealth":
        snapshot = cls._from_payload(payload)
        if str(payload.get("artifact_sha256")) != snapshot.artifact_sha256:
            raise ValueError("durable workload health failed integrity verification")
        return snapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "window_duration_seconds": self.window_duration_seconds,
            "job_count": self.job_count,
            "terminal_job_count": self.terminal_job_count,
            "active_job_count": self.active_job_count,
            "status_counts": dict(self.status_counts),
            "event_count": self.event_count,
            "retry_job_count": self.retry_job_count,
            "lease_recovery_job_count": self.lease_recovery_job_count,
            "terminal_success_rate": self.terminal_success_rate,
            "terminal_failure_rate": self.terminal_failure_rate,
            "dead_letter_rate": self.dead_letter_rate,
            "timeout_rate": self.timeout_rate,
            "cancellation_rate": self.cancellation_rate,
            "retry_job_rate": self.retry_job_rate,
            "lease_recovery_job_rate": self.lease_recovery_job_rate,
            "queue_wait_ms": self.queue_wait_ms.to_dict(),
            "run_duration_ms": self.run_duration_ms.to_dict(),
            "time_to_terminal_ms": self.time_to_terminal_ms.to_dict(),
            "evidence_sha256": self.evidence_sha256,
            "artifact_sha256": self.artifact_sha256,
        }

    def verify_integrity(self) -> bool:
        payload = self.to_dict()
        observed = payload.pop("artifact_sha256")
        return self.version == WORKLOAD_HEALTH_VERSION and observed == _canonical_sha256(
            payload
        )


def _project_record(
    record: PlanningJobWindowEvidence,
    *,
    window_end: datetime,
) -> tuple[dict[str, Any], dict[str, float | None]]:
    if not record.events or record.events[0].event_type != "submitted":
        raise ValueError("workload evidence requires a submitted event")
    previous_id = 0
    previous_time: datetime | None = None
    event_projection = []
    for event in record.events:
        created_at = parse_utc_timestamp(event.created_at)
        if created_at >= window_end:
            raise ValueError("workload event falls outside the as-of window")
        if event.job_id != record.job_id:
            raise ValueError("workload event belongs to another job")
        if event.event_type not in _EVENT_TYPES:
            raise ValueError("workload event type is invalid")
        if event.event_id <= previous_id:
            raise ValueError("workload event IDs must be strictly increasing")
        if previous_time is not None and created_at < previous_time:
            raise ValueError("workload event timestamps must be monotonic")
        if event.attempt < 0:
            raise ValueError("workload event attempt must be non-negative")
        event_projection.append(
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "attempt": event.attempt,
                "created_at": canonical_timestamp(created_at),
            }
        )
        previous_id = event.event_id
        previous_time = created_at

    submitted_at = parse_utc_timestamp(record.events[0].created_at)
    record_created_at = parse_utc_timestamp(record.created_at)
    if submitted_at < record_created_at:
        raise ValueError("workload submitted event predates job creation")
    first_claim = next(
        (event for event in record.events if event.event_type == "claimed"),
        None,
    )
    terminal = next(
        (
            event
            for event in reversed(record.events)
            if event.event_type == record.status and record.status in _TERMINAL_STATUSES
        ),
        None,
    )
    observed_terminal_types = {
        event.event_type
        for event in record.events
        if event.event_type in _TERMINAL_STATUSES
    }
    if record.status in _ACTIVE_STATUSES and observed_terminal_types:
        raise ValueError("active workload job contains a terminal event")
    if record.status in _TERMINAL_STATUSES and not observed_terminal_types:
        raise ValueError("terminal workload job is missing its matching event")
    if record.status in _TERMINAL_STATUSES and observed_terminal_types != {
        record.status
    }:
        raise ValueError("terminal workload job contains conflicting terminal events")
    if derive_status_from_events(record.events) != record.status:
        raise ValueError("workload job status does not match its as-of event prefix")
    terminal_at = parse_utc_timestamp(terminal.created_at) if terminal else None
    claimed_at = parse_utc_timestamp(first_claim.created_at) if first_claim else None
    return (
        {
            "job_id_sha256": hashlib.sha256(record.job_id.encode("utf-8")).hexdigest(),
            "status": record.status,
            "created_at": canonical_timestamp(parse_utc_timestamp(record.created_at)),
            "events": event_projection,
        },
        {
            "queue_wait_ms": _elapsed_ms(submitted_at, claimed_at),
            "run_duration_ms": _elapsed_ms(claimed_at, terminal_at),
            "time_to_terminal_ms": _elapsed_ms(submitted_at, terminal_at),
        },
    )


def derive_status_from_events(events: Sequence[PlanningJobEvent]) -> str:
    """Rebuild one job status from an append-only event prefix."""
    if not events or events[0].event_type != "submitted":
        raise ValueError("workload evidence requires a submitted event")
    status = "queued"
    for event in events[1:]:
        if event.event_type in {"claimed", "lease_reclaimed"}:
            status = "running"
        elif event.event_type == "retry_scheduled":
            status = "queued"
        elif event.event_type in _TERMINAL_STATUSES:
            status = event.event_type
    return status


def _elapsed_ms(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    if end < start:
        raise ValueError("workload timing evidence cannot move backwards")
    return round((end - start).total_seconds() * 1000, 3)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _distribution(values: Sequence[float]) -> LatencyDistribution:
    if not values:
        return LatencyDistribution(0, None, None, None, None, None)
    ordered = sorted(values)
    return LatencyDistribution(
        sample_count=len(ordered),
        minimum_ms=ordered[0],
        p50_ms=_nearest_rank(ordered, 0.50),
        p95_ms=_nearest_rank(ordered, 0.95),
        p99_ms=_nearest_rank(ordered, 0.99),
        maximum_ms=ordered[-1],
    )


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    index = max(0, math.ceil(quantile * len(values)) - 1)
    return values[index]
