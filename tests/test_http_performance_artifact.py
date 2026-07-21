from __future__ import annotations

import copy

import pytest

from evals.performance.verify import (
    PerformanceArtifactError,
    seal_performance_artifact,
    verify_performance_artifact,
)


def _artifact() -> dict:
    payload = {"user_input": "fixture"}
    artifact = {
        "schema_id": "bj-pal.http-performance-artifact",
        "schema_version": 1,
        "run": {
            "transport": "in_process_asgi",
            "backend": "mock",
            "data_profile": {
                "classification": "synthetic",
                "public_reproducible": True,
            },
        },
        "scope_warning": "fixture only; not an SLA",
        "workload": {
            "method": "POST",
            "endpoint": "/v1/plans",
            "total_requests": 2,
            "concurrency": 2,
            "warmup_requests": 0,
            "timeout_seconds": 10.0,
            "latency_scope": "request_after_semaphore_acquire",
            "request_id_prefix": "bench-",
            "payload": payload,
            "payload_sha256": "d3ab85d412476890c6b46d89eea203dd93a153b6634a71cee96e243d244e899e",
        },
        "raw_requests": [
            {
                "request_index": 0,
                "request_id": "bench-0000",
                "status_code": 200,
                "echoed_request_id": "bench-0000",
                "latency_ms": 10.0,
                "error_code": None,
            },
            {
                "request_index": 1,
                "request_id": "bench-0001",
                "status_code": 200,
                "echoed_request_id": "bench-0001",
                "latency_ms": 20.0,
                "error_code": None,
            },
        ],
        "measurement": {
            "wall_seconds": 0.025,
            "summary": {
                "total_requests": 2,
                "successes": 2,
                "failures": 0,
                "request_id_mismatches": 0,
                "error_rate": 0.0,
                "throughput_rps": 80.0,
                "latency_ms": {
                    "method": "nearest_rank",
                    "min": 10.0,
                    "p50": 10.0,
                    "p95": 20.0,
                    "p99": 20.0,
                    "max": 20.0,
                },
                "gate_pass": True,
            },
        },
    }
    return seal_performance_artifact(artifact)


def _socket_artifact() -> dict:
    artifact = copy.deepcopy(_artifact())
    artifact["run"]["transport"] = "localhost_tcp"
    artifact["run"]["runtime_isolation"] = "temporary_isolated_runtime"
    artifact["run"]["server_process"] = {
        "kind": "uvicorn_subprocess",
        "bind_host": "127.0.0.1",
        "network_scope": "ipv4_loopback_only",
        "startup_probe_endpoint": "/readyz",
        "startup_probe_status_code": 200,
        "startup_probe_body_status": "ready",
        "startup_ms": 125.0,
        "shutdown_method": "sigint_and_wait",
        "shutdown_exit_code": 0,
    }
    artifact["workload"]["request_id_prefix"] = "socket-bench-"
    for index, request in enumerate(artifact["raw_requests"]):
        request_id = f"socket-bench-{index:04d}"
        request["request_id"] = request_id
        request["echoed_request_id"] = request_id
    return seal_performance_artifact(artifact)


def test_performance_artifact_recomputes_raw_requests() -> None:
    summary = verify_performance_artifact(_artifact())
    assert summary["gate_pass"] is True
    assert summary["throughput_rps"] == 80.0
    assert summary["latency_ms"]["p95"] == 20.0


def test_performance_artifact_rejects_hash_tampering() -> None:
    artifact = _artifact()
    artifact["raw_requests"][0]["latency_ms"] = 999
    with pytest.raises(PerformanceArtifactError, match="payload_sha256 mismatch"):
        verify_performance_artifact(artifact)


def test_resealed_stale_performance_summary_is_rejected() -> None:
    artifact = copy.deepcopy(_artifact())
    artifact["measurement"]["summary"]["successes"] = 1
    seal_performance_artifact(artifact)
    with pytest.raises(PerformanceArtifactError, match="summary disagrees"):
        verify_performance_artifact(artifact)


def test_resealed_workload_payload_with_stale_hash_is_rejected() -> None:
    artifact = _artifact()
    artifact["workload"]["payload"]["user_input"] = "changed"
    seal_performance_artifact(artifact)
    with pytest.raises(PerformanceArtifactError, match="workload.payload_sha256 mismatch"):
        verify_performance_artifact(artifact)


def test_request_id_mismatch_is_valid_evidence_but_fails_gate() -> None:
    artifact = _artifact()
    artifact["raw_requests"][1]["echoed_request_id"] = "wrong"
    artifact["measurement"]["summary"]["request_id_mismatches"] = 1
    artifact["measurement"]["summary"]["gate_pass"] = False
    seal_performance_artifact(artifact)
    summary = verify_performance_artifact(artifact)
    assert summary["gate_pass"] is False
    assert summary["request_id_mismatches"] == 1


def test_socket_artifact_requires_loopback_subprocess_lifecycle_evidence() -> None:
    summary = verify_performance_artifact(_socket_artifact())
    assert summary["gate_pass"] is True


def test_resealed_socket_artifact_with_non_loopback_bind_is_rejected() -> None:
    artifact = _socket_artifact()
    artifact["run"]["server_process"]["bind_host"] = "0.0.0.0"
    seal_performance_artifact(artifact)

    with pytest.raises(PerformanceArtifactError, match="loopback"):
        verify_performance_artifact(artifact)


def test_resealed_socket_artifact_without_runtime_isolation_is_rejected() -> None:
    artifact = _socket_artifact()
    artifact["run"]["runtime_isolation"] = "default_runtime"
    seal_performance_artifact(artifact)

    with pytest.raises(PerformanceArtifactError, match="runtime isolation"):
        verify_performance_artifact(artifact)
