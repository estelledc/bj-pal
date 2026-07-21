"""Independently verify bounded PostgreSQL connection-pool evidence."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence


SCHEMA_ID = "bj-pal.postgres-pool-acceptance"
SCHEMA_VERSION = 1


class PostgresPoolArtifactError(ValueError):
    """The artifact is malformed, tampered with, or fails its bounded gate."""


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


def seal_postgres_pool_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    artifact.pop("integrity", None)
    artifact["integrity"] = {
        "algorithm": "sha256",
        "payload_sha256": canonical_artifact_sha256(artifact),
    }
    return artifact


def _nearest_rank(values: Sequence[float], percentile: int) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def _finite_non_negative(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise PostgresPoolArtifactError(f"{field} must be finite and non-negative")
    return float(value)


def verify_postgres_pool_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    if artifact.get("schema_id") != SCHEMA_ID:
        raise PostgresPoolArtifactError("unsupported schema_id")
    if artifact.get("schema_version") != SCHEMA_VERSION:
        raise PostgresPoolArtifactError("unsupported schema_version")
    if artifact.get("classification") != "controlled_real_database_acceptance":
        raise PostgresPoolArtifactError("classification must identify real database acceptance")
    warning = artifact.get("scope_warning")
    if not isinstance(warning, str) or "not production capacity" not in warning:
        raise PostgresPoolArtifactError("scope_warning must reject production-capacity claims")
    integrity = artifact.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256":
        raise PostgresPoolArtifactError("integrity block is missing")
    if integrity.get("payload_sha256") != canonical_artifact_sha256(artifact):
        raise PostgresPoolArtifactError("payload_sha256 mismatch")

    database = artifact.get("database")
    if not isinstance(database, Mapping) or database.get("product") != "PostgreSQL":
        raise PostgresPoolArtifactError("database product must be PostgreSQL")
    major = database.get("server_major")
    if not isinstance(major, int) or major < 17:
        raise PostgresPoolArtifactError("PostgreSQL 17 or newer is required")
    if any(key in database for key in ("dsn", "host", "port", "user", "password")):
        raise PostgresPoolArtifactError("database metadata must not contain connection details")

    configuration = artifact.get("configuration")
    if not isinstance(configuration, Mapping):
        raise PostgresPoolArtifactError("configuration is missing")
    min_size = configuration.get("min_size")
    max_size = configuration.get("max_size")
    max_waiting = configuration.get("max_waiting")
    timeout = _finite_non_negative(
        configuration.get("timeout_seconds"), field="configuration.timeout_seconds"
    )
    if not isinstance(min_size, int) or not isinstance(max_size, int):
        raise PostgresPoolArtifactError("pool sizes must be integers")
    if not 0 <= min_size <= max_size <= 64 or max_size < 1:
        raise PostgresPoolArtifactError("pool sizes are out of bounds")
    if not isinstance(max_waiting, int) or not 1 <= max_waiting <= 256:
        raise PostgresPoolArtifactError("max_waiting is out of bounds")
    if timeout <= 0:
        raise PostgresPoolArtifactError("timeout_seconds must be positive")

    backpressure = artifact.get("backpressure")
    if not isinstance(backpressure, Mapping):
        raise PostgresPoolArtifactError("backpressure evidence is missing")
    if backpressure.get("held_connections") != max_size:
        raise PostgresPoolArtifactError("backpressure must hold the configured maximum")
    if backpressure.get("timeout_error_code") != "pool_acquisition_timeout":
        raise PostgresPoolArtifactError("stable timeout error code is missing")
    if backpressure.get("timeout_error_message") != (
        "PostgreSQL durable job store connection acquisition timed out"
    ):
        raise PostgresPoolArtifactError("stable timeout error message is missing")
    elapsed = _finite_non_negative(
        backpressure.get("timeout_elapsed_seconds"),
        field="backpressure.timeout_elapsed_seconds",
    )
    if elapsed < timeout * 0.8:
        raise PostgresPoolArtifactError("pool timeout returned before the configured bound")
    if backpressure.get("dsn_exposed") is not False:
        raise PostgresPoolArtifactError("backpressure error exposed connection details")
    if backpressure.get("queue_full_error_code") != "pool_wait_queue_full":
        raise PostgresPoolArtifactError("stable queue-full error code is missing")
    if backpressure.get("queue_full_error_message") != (
        "PostgreSQL durable job store connection queue is full"
    ):
        raise PostgresPoolArtifactError("stable queue-full error message is missing")
    if backpressure.get("queue_full_dsn_exposed") is not False:
        raise PostgresPoolArtifactError("queue-full error exposed connection details")
    if backpressure.get("probe_after_release") is not True:
        raise PostgresPoolArtifactError("pool did not recover after capacity was released")

    replacement = artifact.get("connection_replacement")
    if not isinstance(replacement, Mapping):
        raise PostgresPoolArtifactError("connection replacement evidence is missing")
    if replacement.get("backend_terminated") is not True:
        raise PostgresPoolArtifactError("database backend was not terminated")
    if replacement.get("probe_after_termination") is not True:
        raise PostgresPoolArtifactError("pool did not recover after termination")
    if replacement.get("backend_identity_changed") is not True:
        raise PostgresPoolArtifactError("replacement connection was not independently observed")

    workload = artifact.get("workload")
    if not isinstance(workload, Mapping):
        raise PostgresPoolArtifactError("workload evidence is missing")
    total = workload.get("total_operations")
    concurrency = workload.get("concurrency")
    latencies = workload.get("latency_ms")
    if not isinstance(total, int) or total < 20:
        raise PostgresPoolArtifactError("at least 20 operations are required")
    if not isinstance(concurrency, int) or not 1 <= concurrency <= total:
        raise PostgresPoolArtifactError("workload concurrency is invalid")
    if not isinstance(latencies, list) or len(latencies) != total:
        raise PostgresPoolArtifactError("latency sample count is inconsistent")
    samples = [
        _finite_non_negative(value, field=f"workload.latency_ms[{index}]")
        for index, value in enumerate(latencies)
    ]
    if workload.get("successes") != total or workload.get("failures") != 0:
        raise PostgresPoolArtifactError("bounded workload contains failures")
    summary = workload.get("summary")
    expected_summary = {
        "min_ms": round(min(samples), 3),
        "p50_ms": round(_nearest_rank(samples, 50), 3),
        "p95_ms": round(_nearest_rank(samples, 95), 3),
        "max_ms": round(max(samples), 3),
    }
    if summary != expected_summary:
        raise PostgresPoolArtifactError("latency summary disagrees with raw samples")

    shutdown = artifact.get("shutdown")
    if not isinstance(shutdown, Mapping):
        raise PostgresPoolArtifactError("shutdown evidence is missing")
    if shutdown.get("closed") is not True or shutdown.get("new_work_rejected") is not True:
        raise PostgresPoolArtifactError("closed pool accepted new work")
    if shutdown.get("sessions_after_close") != shutdown.get("sessions_before_open"):
        raise PostgresPoolArtifactError("pool shutdown leaked database sessions")

    return {
        "gate_pass": True,
        "total_operations": total,
        "p95_ms": expected_summary["p95_ms"],
        "pool_max_size": max_size,
    }
