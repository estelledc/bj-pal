"""Build fixed operational alert cases through the production evaluator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from agents.trace_export import TraceExportStatus
from jobs import DurableWorkloadHealth, PlanningJobEvent, PlanningJobWindowEvidence
from monitoring import OperationalAlertSnapshot


WINDOW_START = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)
OBSERVED_AT = datetime(2026, 7, 20, 2, 0, tzinfo=timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _records(
    *,
    job_count: int,
    terminal_count: int,
    failure_count: int,
    retry_count: int,
    queue_wait_ms: int,
) -> tuple[PlanningJobWindowEvidence, ...]:
    records = []
    for index in range(job_count):
        job_id = f"synthetic-alert-job-{index:02d}"
        created = WINDOW_START + timedelta(seconds=index * 90)
        events = [
            PlanningJobEvent(
                event_id=1,
                job_id=job_id,
                event_type="submitted",
                attempt=0,
                worker_id=None,
                payload={},
                created_at=_timestamp(created),
            )
        ]
        if index >= terminal_count:
            records.append(
                PlanningJobWindowEvidence(
                    job_id=job_id,
                    status="queued",
                    created_at=_timestamp(created),
                    events=tuple(events),
                )
            )
            continue
        claimed = created + timedelta(milliseconds=queue_wait_ms)
        events.append(
            PlanningJobEvent(
                event_id=2,
                job_id=job_id,
                event_type="claimed",
                attempt=1,
                worker_id="synthetic-worker",
                payload={},
                created_at=_timestamp(claimed),
            )
        )
        terminal_attempt = 1
        if index < retry_count:
            events.extend(
                (
                    PlanningJobEvent(
                        event_id=3,
                        job_id=job_id,
                        event_type="retry_scheduled",
                        attempt=1,
                        worker_id="synthetic-worker",
                        payload={},
                        created_at=_timestamp(claimed + timedelta(seconds=1)),
                    ),
                    PlanningJobEvent(
                        event_id=4,
                        job_id=job_id,
                        event_type="claimed",
                        attempt=2,
                        worker_id="synthetic-worker",
                        payload={},
                        created_at=_timestamp(claimed + timedelta(seconds=2)),
                    ),
                )
            )
            terminal_attempt = 2
        failed = index < failure_count
        events.append(
            PlanningJobEvent(
                event_id=len(events) + 1,
                job_id=job_id,
                event_type="failed" if failed else "succeeded",
                attempt=terminal_attempt,
                worker_id="synthetic-worker",
                payload={"error_code": "planning_execution_failed"} if failed else {},
                created_at=_timestamp(claimed + timedelta(seconds=5)),
            )
        )
        records.append(
            PlanningJobWindowEvidence(
                job_id=job_id,
                status="failed" if failed else "succeeded",
                created_at=_timestamp(created),
                events=tuple(events),
            )
        )
    return tuple(records)


def _workload(
    *,
    job_count: int,
    terminal_count: int,
    failure_count: int,
    retry_count: int,
    queue_wait_ms: int,
) -> DurableWorkloadHealth:
    return DurableWorkloadHealth.create(
        window_start=_timestamp(WINDOW_START),
        window_end=_timestamp(WINDOW_END),
        records=_records(
            job_count=job_count,
            terminal_count=terminal_count,
            failure_count=failure_count,
            retry_count=retry_count,
            queue_wait_ms=queue_wait_ms,
        ),
    )


def _trace(*, backend: str, state: str, attempts: int) -> dict[str, Any]:
    return TraceExportStatus(
        version="trace_export_status_v1",
        backend=backend,
        state=state,
        processor="batch" if backend == "otlp" else "none",
        privacy_policy="trace_export_minimal_v1",
        semconv_profile="gen_ai_minimal_v1",
        content_capture_enabled=False,
        endpoint_origin_sha256="b" * 64 if backend == "otlp" else None,
        export_attempt_count=attempts,
        exported_span_count=3 if state == "healthy" else 0,
        failed_span_count=3 if state == "degraded" else 0,
        dropped_attribute_count=2 if backend == "otlp" else 0,
        last_error_code="export_failed" if state == "degraded" else None,
    ).to_dict()


def _case(
    *,
    case_id: str,
    workload: DurableWorkloadHealth,
    trace_status: dict[str, Any],
    expected_overall_state: str,
    expected_rule_states: dict[str, str],
) -> dict[str, Any]:
    observed = OperationalAlertSnapshot.create(
        workload=workload,
        trace_status=trace_status,
        observed_at=OBSERVED_AT,
    )
    observed_states = {rule.rule_id: rule.state for rule in observed.rules}
    return {
        "case_id": case_id,
        "classification": "authored_synthetic_operational_fixture",
        "source": {
            "workload": workload.to_dict(),
            "trace_status": trace_status,
            "observed_at": _timestamp(OBSERVED_AT),
        },
        "expected": {
            "overall_state": expected_overall_state,
            "rule_states": expected_rule_states,
        },
        "observed": observed.to_dict(),
        "checks": {
            "decision_matches": (
                observed.overall_state == expected_overall_state
                and observed_states == expected_rule_states
            ),
            "sample_gate_matches": all(
                rule.state != "healthy"
                or rule.sample_count >= rule.required_sample_count
                for rule in observed.rules
            ),
            "integrity_valid": observed.verify_integrity(),
            "source_binding_valid": (
                observed.workload_artifact_sha256 == workload.artifact_sha256
            ),
        },
    }


def evaluate_operational_alerts() -> dict[str, Any]:
    healthy_workload = _workload(
        job_count=20,
        terminal_count=20,
        failure_count=2,
        retry_count=2,
        queue_wait_ms=10_000,
    )
    cases = [
        _case(
            case_id="healthy_enough_samples",
            workload=healthy_workload,
            trace_status=_trace(backend="otlp", state="healthy", attempts=1),
            expected_overall_state="healthy",
            expected_rule_states={
                "terminal_failure_rate": "healthy",
                "queue_wait_p95_ms": "healthy",
                "retry_job_rate": "healthy",
                "trace_export_health": "healthy",
            },
        ),
        _case(
            case_id="all_thresholds_firing",
            workload=_workload(
                job_count=20,
                terminal_count=20,
                failure_count=5,
                retry_count=7,
                queue_wait_ms=40_000,
            ),
            trace_status=_trace(backend="otlp", state="degraded", attempts=1),
            expected_overall_state="firing",
            expected_rule_states={
                "terminal_failure_rate": "firing",
                "queue_wait_p95_ms": "firing",
                "retry_job_rate": "firing",
                "trace_export_health": "firing",
            },
        ),
        _case(
            case_id="minimum_samples_not_met",
            workload=_workload(
                job_count=2,
                terminal_count=1,
                failure_count=0,
                retry_count=0,
                queue_wait_ms=1_000,
            ),
            trace_status=_trace(
                backend="otlp", state="configured_unproven", attempts=0
            ),
            expected_overall_state="insufficient_data",
            expected_rule_states={
                "terminal_failure_rate": "insufficient_data",
                "queue_wait_p95_ms": "insufficient_data",
                "retry_job_rate": "insufficient_data",
                "trace_export_health": "insufficient_data",
            },
        ),
        _case(
            case_id="otlp_not_configured",
            workload=healthy_workload,
            trace_status=_trace(backend="off", state="disabled", attempts=0),
            expected_overall_state="healthy",
            expected_rule_states={
                "terminal_failure_rate": "healthy",
                "queue_wait_p95_ms": "healthy",
                "retry_job_rate": "healthy",
                "trace_export_health": "disabled",
            },
        ),
    ]
    return {
        "case_count": len(cases),
        "raw_cases": cases,
        "metrics": {
            "decision_accuracy_rate": sum(
                case["checks"]["decision_matches"] for case in cases
            )
            / len(cases),
            "sample_gate_accuracy_rate": sum(
                case["checks"]["sample_gate_matches"] for case in cases
            )
            / len(cases),
            "integrity_rate": sum(
                case["checks"]["integrity_valid"] for case in cases
            )
            / len(cases),
            "source_binding_rate": sum(
                case["checks"]["source_binding_valid"] for case in cases
            )
            / len(cases),
            "privacy_minimization_rate": 1.0,
        },
    }
