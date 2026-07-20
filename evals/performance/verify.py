"""Independently recompute an HTTP benchmark artifact from raw requests."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_ID = "bj-pal.http-performance-artifact"
SCHEMA_VERSION = 1


class PerformanceArtifactError(ValueError):
    """The artifact is malformed, tampered with, or internally inconsistent."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_artifact_sha256(artifact: Mapping[str, Any]) -> str:
    body = dict(artifact)
    body.pop("integrity", None)
    return hashlib.sha256(_canonical_json(body)).hexdigest()


def _value_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def seal_performance_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    artifact.pop("integrity", None)
    artifact["integrity"] = {
        "algorithm": "sha256",
        "payload_sha256": canonical_artifact_sha256(artifact),
    }
    return artifact


def _nearest_rank(values: Sequence[float], percentile: int) -> float:
    if not values:
        raise PerformanceArtifactError("raw_requests must not be empty")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def _round(value: float) -> float:
    return round(value, 6)


def _recompute_summary(
    raw_requests: Sequence[Mapping[str, Any]],
    *,
    wall_seconds: float,
    request_id_prefix: str,
) -> dict[str, Any]:
    if not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise PerformanceArtifactError("measurement.wall_seconds must be positive and finite")

    if any(not isinstance(item, Mapping) for item in raw_requests):
        raise PerformanceArtifactError("every raw_requests item must be an object")
    expected_indices = list(range(len(raw_requests)))
    indices = [item.get("request_index") for item in raw_requests]
    if indices != expected_indices:
        raise PerformanceArtifactError("raw request indices must be ordered, unique, and contiguous")

    latencies: list[float] = []
    successes = 0
    request_id_mismatches = 0
    for index, item in enumerate(raw_requests):
        latency = item.get("latency_ms")
        if not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0:
            raise PerformanceArtifactError(f"raw_requests[{index}].latency_ms is invalid")
        latencies.append(float(latency))

        expected_request_id = f"{request_id_prefix}{index:04d}"
        if item.get("request_id") != expected_request_id:
            raise PerformanceArtifactError(
                f"raw_requests[{index}].request_id does not match the workload prefix"
            )
        if item.get("echoed_request_id") != expected_request_id:
            request_id_mismatches += 1

        status_code = item.get("status_code")
        error_code = item.get("error_code")
        if not isinstance(status_code, int) or not 0 <= status_code <= 599:
            raise PerformanceArtifactError(f"raw_requests[{index}].status_code is invalid")
        if error_code is not None and not isinstance(error_code, str):
            raise PerformanceArtifactError(f"raw_requests[{index}].error_code is invalid")
        if status_code == 200 and error_code is None:
            successes += 1

    total_requests = len(raw_requests)
    failures = total_requests - successes
    return {
        "total_requests": total_requests,
        "successes": successes,
        "failures": failures,
        "request_id_mismatches": request_id_mismatches,
        "error_rate": _round(failures / total_requests),
        "throughput_rps": _round(total_requests / wall_seconds),
        "latency_ms": {
            "method": "nearest_rank",
            "min": _round(min(latencies)),
            "p50": _round(_nearest_rank(latencies, 50)),
            "p95": _round(_nearest_rank(latencies, 95)),
            "p99": _round(_nearest_rank(latencies, 99)),
            "max": _round(max(latencies)),
        },
        "gate_pass": failures == 0 and request_id_mismatches == 0,
    }


def verify_performance_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Verify integrity and recompute every claim from per-request evidence."""
    if artifact.get("schema_id") != SCHEMA_ID:
        raise PerformanceArtifactError(f"unsupported schema_id: {artifact.get('schema_id')!r}")
    if artifact.get("schema_version") != SCHEMA_VERSION:
        raise PerformanceArtifactError(
            f"unsupported schema_version: {artifact.get('schema_version')!r}"
        )

    integrity = artifact.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256":
        raise PerformanceArtifactError("integrity block is missing or unsupported")
    if integrity.get("payload_sha256") != canonical_artifact_sha256(artifact):
        raise PerformanceArtifactError("payload_sha256 mismatch")

    run = artifact.get("run")
    if not isinstance(run, Mapping):
        raise PerformanceArtifactError("run metadata is missing")
    if run.get("backend") != "mock":
        raise PerformanceArtifactError("public benchmark must use the mock backend")
    transport = run.get("transport")
    if transport == "in_process_asgi":
        if "server_process" in run:
            raise PerformanceArtifactError(
                "in-process benchmark must not claim a server subprocess"
            )
    elif transport == "localhost_tcp":
        if run.get("runtime_isolation") != "temporary_isolated_runtime":
            raise PerformanceArtifactError(
                "localhost TCP benchmark requires temporary runtime isolation"
            )
        server_process = run.get("server_process")
        if not isinstance(server_process, Mapping):
            raise PerformanceArtifactError(
                "localhost TCP benchmark requires server subprocess evidence"
            )
        if (
            server_process.get("kind") != "uvicorn_subprocess"
            or server_process.get("bind_host") != "127.0.0.1"
            or server_process.get("network_scope") != "ipv4_loopback_only"
        ):
            raise PerformanceArtifactError(
                "localhost TCP benchmark must bind only to the IPv4 loopback address"
            )
        startup_ms = server_process.get("startup_ms")
        if (
            isinstance(startup_ms, bool)
            or not isinstance(startup_ms, (int, float))
            or not math.isfinite(startup_ms)
            or startup_ms <= 0
        ):
            raise PerformanceArtifactError("server startup_ms must be positive and finite")
        if (
            server_process.get("startup_probe_endpoint") != "/readyz"
            or server_process.get("startup_probe_status_code") != 200
            or server_process.get("startup_probe_body_status") != "ready"
        ):
            raise PerformanceArtifactError(
                "localhost TCP benchmark requires a successful readiness probe"
            )
        if (
            server_process.get("shutdown_method") != "sigint_and_wait"
            or server_process.get("shutdown_exit_code") != 0
        ):
            raise PerformanceArtifactError(
                "localhost TCP benchmark requires a clean, waited server shutdown"
            )
    else:
        raise PerformanceArtifactError(f"unsupported benchmark transport: {transport!r}")
    profile = run.get("data_profile")
    if (
        not isinstance(profile, Mapping)
        or profile.get("public_reproducible") is not True
        or profile.get("classification") != "synthetic"
    ):
        raise PerformanceArtifactError(
            "public benchmark requires a reproducible synthetic data profile"
        )
    scope_warning = artifact.get("scope_warning")
    if not isinstance(scope_warning, str) or not scope_warning.strip():
        raise PerformanceArtifactError("scope_warning is missing")

    workload = artifact.get("workload")
    if not isinstance(workload, Mapping):
        raise PerformanceArtifactError("workload metadata is missing")
    if workload.get("endpoint") != "/v1/plans" or workload.get("method") != "POST":
        raise PerformanceArtifactError("unsupported benchmark workload")
    total_requests = workload.get("total_requests")
    concurrency = workload.get("concurrency")
    if not isinstance(total_requests, int) or total_requests < 1:
        raise PerformanceArtifactError("workload.total_requests must be a positive integer")
    if not isinstance(concurrency, int) or not 1 <= concurrency <= total_requests:
        raise PerformanceArtifactError("workload.concurrency is out of range")
    warmup_requests = workload.get("warmup_requests")
    if not isinstance(warmup_requests, int) or warmup_requests < 0:
        raise PerformanceArtifactError("workload.warmup_requests is invalid")
    timeout_seconds = workload.get("timeout_seconds")
    if (
        not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise PerformanceArtifactError("workload.timeout_seconds is invalid")
    if workload.get("latency_scope") != "request_after_semaphore_acquire":
        raise PerformanceArtifactError("workload.latency_scope is unsupported")
    request_id_prefix = workload.get("request_id_prefix")
    if not isinstance(request_id_prefix, str) or not request_id_prefix:
        raise PerformanceArtifactError("workload.request_id_prefix is missing")
    if transport == "localhost_tcp" and request_id_prefix != "socket-bench-":
        raise PerformanceArtifactError(
            "localhost TCP benchmark requires the socket request-id prefix"
        )
    payload = workload.get("payload")
    if not isinstance(payload, Mapping):
        raise PerformanceArtifactError("workload.payload is missing")
    if workload.get("payload_sha256") != _value_sha256(payload):
        raise PerformanceArtifactError("workload.payload_sha256 mismatch")

    raw_requests = artifact.get("raw_requests")
    if not isinstance(raw_requests, list) or len(raw_requests) != total_requests:
        raise PerformanceArtifactError("raw_requests count does not match workload.total_requests")
    measurement = artifact.get("measurement")
    if not isinstance(measurement, Mapping):
        raise PerformanceArtifactError("measurement is missing")
    wall_seconds = measurement.get("wall_seconds")
    if not isinstance(wall_seconds, (int, float)):
        raise PerformanceArtifactError("measurement.wall_seconds is invalid")

    recomputed = _recompute_summary(
        raw_requests,
        wall_seconds=float(wall_seconds),
        request_id_prefix=request_id_prefix,
    )
    if measurement.get("summary") != recomputed:
        raise PerformanceArtifactError("measurement.summary disagrees with raw requests")
    return recomputed


def read_performance_artifact(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
