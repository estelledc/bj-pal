"""Server-Sent Event projection for durable planning job events."""

from __future__ import annotations

import json

from jobs import PlanningJobEvent


TERMINAL_JOB_STATUSES = frozenset(
    {"succeeded", "failed", "dead_lettered", "cancelled", "timed_out"}
)


def encode_job_event(event: PlanningJobEvent, *, retry_ms: int = 1000) -> str:
    """Encode one persisted event; its SQLite event_id is the SSE resume cursor."""
    payload = {
        "event_id": event.event_id,
        "job_id": event.job_id,
        "event_type": event.event_type,
        "attempt": event.attempt,
        "worker_id": event.worker_id,
        "payload": event.payload,
        "created_at": event.created_at,
    }
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        f"id: {event.event_id}\n"
        f"event: {event.event_type}\n"
        f"retry: {retry_ms}\n"
        f"data: {data}\n\n"
    )


def encode_stream_timeout(cursor: int) -> str:
    """A comment is transport metadata, not a durable domain event."""
    return f": stream-timeout cursor={cursor}\n\n"


def encode_stream_error(cursor: int) -> str:
    """Tell the client to reconnect without inventing or exposing a domain error."""
    return f": stream-error cursor={cursor}\n\n"
