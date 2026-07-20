from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.trace_export import TraceExportStatus  # noqa: E402
from jobs import DurableWorkloadHealth, LatencyDistribution  # noqa: E402
from monitoring import OperationalAlertPolicy, OperationalAlertSnapshot  # noqa: E402


def _distribution(count: int, p95: float | None) -> LatencyDistribution:
    value = p95 if count else None
    return LatencyDistribution(
        sample_count=count,
        minimum_ms=value,
        p50_ms=value,
        p95_ms=value,
        p99_ms=value,
        maximum_ms=value,
    )


def _workload(
    *,
    jobs: int = 20,
    terminal: int = 20,
    failure_rate: float | None = 0.1,
    retry_rate: float | None = 0.1,
    queue_samples: int = 20,
    queue_p95: float | None = 10_000.0,
) -> DurableWorkloadHealth:
    payload = {
        "version": "durable_workload_health_v1",
        "window_start": "2026-07-20T00:00:00.000Z",
        "window_end": "2026-07-20T01:00:00.000Z",
        "window_duration_seconds": 3600,
        "job_count": jobs,
        "terminal_job_count": terminal,
        "active_job_count": jobs - terminal,
        "status_counts": {
            "queued": jobs - terminal,
            "running": 0,
            "succeeded": terminal,
            "failed": 0,
            "dead_lettered": 0,
            "cancelled": 0,
            "timed_out": 0,
        },
        "event_count": jobs * 3,
        "retry_job_count": round((retry_rate or 0) * jobs),
        "lease_recovery_job_count": 0,
        "terminal_success_rate": None if failure_rate is None else 1 - failure_rate,
        "terminal_failure_rate": failure_rate,
        "dead_letter_rate": 0.0 if terminal else None,
        "timeout_rate": 0.0 if terminal else None,
        "cancellation_rate": 0.0 if terminal else None,
        "retry_job_rate": retry_rate,
        "lease_recovery_job_rate": 0.0 if jobs else None,
        "queue_wait_ms": _distribution(queue_samples, queue_p95).to_dict(),
        "run_duration_ms": _distribution(terminal, 15_000.0 if terminal else None).to_dict(),
        "time_to_terminal_ms": _distribution(
            terminal, 20_000.0 if terminal else None
        ).to_dict(),
        "evidence_sha256": "a" * 64,
    }
    return DurableWorkloadHealth._from_payload(payload)


def _trace(*, state: str = "healthy", attempts: int = 1) -> dict:
    return TraceExportStatus(
        version="trace_export_status_v1",
        backend="otlp",
        state=state,
        processor="batch",
        privacy_policy="trace_export_minimal_v1",
        semconv_profile="gen_ai_minimal_v1",
        content_capture_enabled=False,
        endpoint_origin_sha256="b" * 64,
        export_attempt_count=attempts,
        exported_span_count=3 if state == "healthy" else 0,
        failed_span_count=3 if state == "degraded" else 0,
        dropped_attribute_count=2,
        last_error_code="export_failed" if state == "degraded" else None,
    ).to_dict()


def _snapshot(workload: DurableWorkloadHealth, trace: dict) -> OperationalAlertSnapshot:
    return OperationalAlertSnapshot.create(
        workload=workload,
        trace_status=trace,
        observed_at=datetime(2026, 7, 20, 2, tzinfo=timezone.utc),
    )


def test_healthy_snapshot_evaluates_all_rules_without_identifiers() -> None:
    snapshot = _snapshot(_workload(), _trace())
    serialized = json.dumps(snapshot.to_dict(), ensure_ascii=False)

    assert snapshot.overall_state == "healthy"
    assert snapshot.evaluated_rule_count == 4
    assert snapshot.firing_rule_count == 0
    assert snapshot.insufficient_data_rule_count == 0
    assert snapshot.verify_integrity()
    assert "tenant" not in serialized
    assert "job_id" not in serialized
    assert "endpoint_origin" not in serialized


def test_thresholds_use_gte_and_firing_dominates_insufficient_data() -> None:
    snapshot = _snapshot(
        _workload(
            jobs=20,
            terminal=20,
            failure_rate=0.2,
            retry_rate=0.3,
            queue_samples=19,
            queue_p95=50_000.0,
        ),
        _trace(state="degraded"),
    )
    states = {rule.rule_id: rule.state for rule in snapshot.rules}

    assert snapshot.overall_state == "firing"
    assert states == {
        "terminal_failure_rate": "firing",
        "queue_wait_p95_ms": "insufficient_data",
        "retry_job_rate": "firing",
        "trace_export_health": "firing",
    }
    assert snapshot.firing_rule_count == 3


def test_small_window_and_unproven_export_never_claim_healthy() -> None:
    snapshot = _snapshot(
        _workload(
            jobs=2,
            terminal=1,
            failure_rate=0.0,
            retry_rate=0.0,
            queue_samples=1,
            queue_p95=1.0,
        ),
        _trace(state="configured_unproven", attempts=0),
    )

    assert snapshot.overall_state == "insufficient_data"
    assert snapshot.evaluated_rule_count == 0
    assert snapshot.insufficient_data_rule_count == 4


def test_non_otlp_backend_disables_only_export_rule() -> None:
    trace = _trace()
    trace.update(
        backend="off",
        state="disabled",
        processor="none",
        endpoint_origin_sha256=None,
        export_attempt_count=0,
        exported_span_count=0,
    )
    snapshot = _snapshot(_workload(), trace)

    assert snapshot.overall_state == "healthy"
    assert snapshot.disabled_rule_count == 1
    assert snapshot.rules[-1].reason_code == "otlp_export_not_configured"


def test_invalid_otlp_configuration_fires_instead_of_looking_disabled() -> None:
    trace = _trace(state="degraded", attempts=0)
    trace.update(
        backend="invalid",
        processor="none",
        endpoint_origin_sha256=None,
        failed_span_count=0,
        last_error_code="invalid_otlp_endpoint",
    )
    snapshot = _snapshot(_workload(), trace)

    assert snapshot.overall_state == "firing"
    assert snapshot.rules[-1].state == "firing"
    assert snapshot.rules[-1].reason_code == "trace_export_invalid_configuration"


def test_sources_and_snapshot_fail_closed_on_tampering() -> None:
    workload = _workload()
    tampered_workload = replace(workload, terminal_failure_rate=0.9)
    with pytest.raises(ValueError, match="workload health source"):
        _snapshot(tampered_workload, _trace())

    trace = _trace()
    trace["content_capture_enabled"] = True
    with pytest.raises(ValueError, match="content-capturing"):
        _snapshot(workload, trace)

    payload = _snapshot(workload, _trace()).to_dict()
    payload["overall_state"] = "firing"
    with pytest.raises(ValueError, match="integrity"):
        OperationalAlertSnapshot.from_dict(payload)


def test_policy_and_observation_time_validation_fail_closed() -> None:
    with pytest.raises(ValueError, match="sample floors"):
        OperationalAlertPolicy(minimum_jobs=0).verify()
    with pytest.raises(ValueError, match="closed"):
        OperationalAlertSnapshot.create(
            workload=_workload(),
            trace_status=_trace(),
            observed_at=datetime(2026, 7, 20, 0, 30, tzinfo=timezone.utc),
        )


def test_inconsistent_trace_status_combinations_fail_closed() -> None:
    trace = _trace()
    trace["endpoint_origin_sha256"] = None
    with pytest.raises(ValueError, match="endpoint digest"):
        _snapshot(_workload(), trace)

    trace = _trace(state="degraded")
    trace.update(backend="invalid", state="healthy")
    with pytest.raises(ValueError, match="must be degraded"):
        _snapshot(_workload(), trace)
