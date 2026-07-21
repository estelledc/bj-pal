#!/usr/bin/env python3
"""Run a bounded connection-pool acceptance against a real PostgreSQL server."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

from evals.postgres_pool import seal_postgres_pool_artifact  # noqa: E402
from jobs import JobStoreUnavailable  # noqa: E402
from jobs.postgres_repository import PostgresPlanningJobRepository  # noqa: E402


DSN_ENV = "BJ_PAL_TEST_POSTGRES_DSN"
DEFAULT_OUTPUT = ROOT / "evals" / "results" / "postgres-pool-acceptance.json"
ENVIRONMENT_LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _count_pool_sessions(dsn: str) -> int:
    with psycopg.connect(dsn) as connection:
        return int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM pg_stat_activity
                WHERE application_name = 'bj-pal-job-store'
                """
            ).fetchone()[0]
        )


def _latency_summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)

    def nearest(percentile: int) -> float:
        index = max(0, (percentile * len(ordered) + 99) // 100 - 1)
        return ordered[index]

    return {
        "min_ms": round(ordered[0], 3),
        "p50_ms": round(nearest(50), 3),
        "p95_ms": round(nearest(95), 3),
        "max_ms": round(ordered[-1], 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--operations", type=int, default=64)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--environment-label",
        default="operator_supplied_postgresql",
        help="Non-secret provenance label; it is not an external attestation.",
    )
    args = parser.parse_args()
    if not 20 <= args.operations <= 2_000:
        parser.error("--operations must be between 20 and 2000")
    if not 1 <= args.concurrency <= min(args.operations, 64):
        parser.error("--concurrency must be between 1 and min(operations, 64)")
    if not ENVIRONMENT_LABEL_PATTERN.fullmatch(args.environment_label):
        parser.error(
            "--environment-label must be a lowercase non-secret identifier"
        )
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        parser.error(f"{DSN_ENV} is required")

    schema = f"bjpal_pool_acceptance_{uuid.uuid4().hex}"
    min_size = 1
    max_size = 2
    timeout_seconds = 0.1
    max_waiting = 1
    sessions_before = _count_pool_sessions(dsn)
    repository = PostgresPlanningJobRepository(
        dsn,
        schema=schema,
        pool_min_size=min_size,
        pool_max_size=max_size,
        pool_timeout_seconds=timeout_seconds,
        pool_max_waiting=max_waiting,
    )
    artifact: dict | None = None
    try:
        with psycopg.connect(dsn) as control:
            server_version = str(control.execute("SHOW server_version").fetchone()[0])

        first = repository._connect()
        second = repository._connect()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                started = time.perf_counter()
                waiting = executor.submit(repository.probe)
                deadline = started + timeout_seconds
                while (
                    repository.pool_stats().get("requests_waiting", 0) < 1
                    and time.perf_counter() < deadline
                ):
                    time.sleep(0.001)
                try:
                    repository.probe()
                except JobStoreUnavailable as exc:
                    queue_full_message = str(exc)
                else:
                    raise RuntimeError("pool wait queue unexpectedly accepted excess work")
                try:
                    waiting.result()
                except JobStoreUnavailable as exc:
                    timeout_message = str(exc)
                else:
                    raise RuntimeError("pool capacity probe unexpectedly succeeded")
                timeout_elapsed = time.perf_counter() - started
            if timeout_message != (
                "PostgreSQL durable job store connection acquisition timed out"
            ):
                raise RuntimeError("pool timeout did not use the stable error contract")
            if queue_full_message != (
                "PostgreSQL durable job store connection queue is full"
            ):
                raise RuntimeError("pool queue-full did not use the stable error contract")
        finally:
            second.__exit__(None, None, None)
            first.__exit__(None, None, None)
        probe_after_release = repository.probe()

        with repository._connect() as pooled:
            original_pid = int(
                pooled.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
            )
        with psycopg.connect(dsn, autocommit=True) as control:
            terminated = bool(
                control.execute(
                    "SELECT pg_terminate_backend(%s)", (original_pid,)
                ).fetchone()[0]
            )
        probe_after_termination = repository.probe()
        with repository._connect() as pooled:
            replacement_pid = int(
                pooled.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
            )

        def probe_once(_: int) -> tuple[bool, float]:
            started = time.perf_counter()
            try:
                return repository.probe(), round(
                    (time.perf_counter() - started) * 1_000, 3
                )
            except JobStoreUnavailable:
                return False, round((time.perf_counter() - started) * 1_000, 3)

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            outcomes = list(executor.map(probe_once, range(args.operations)))
        successes = sum(1 for success, _ in outcomes if success)
        latencies = [latency for _, latency in outcomes]
        stats = repository.pool_stats()
        repository.close()
        try:
            repository.probe()
        except JobStoreUnavailable as exc:
            new_work_rejected = "connection pool is closed" in str(exc)
        else:
            new_work_rejected = False
        sessions_after = _count_pool_sessions(dsn)

        artifact = {
            "schema_id": "bj-pal.postgres-pool-acceptance",
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "classification": "controlled_real_database_acceptance",
            "scope_warning": (
                "One bounded run against an operator-supplied PostgreSQL instance proves "
                "pool backpressure, local broken-connection replacement, concurrent probes, "
                "and clean shutdown. It is not production capacity, database HA/failover, "
                "cross-host load, an SLA, or real-user evidence."
            ),
            "database": {
                "product": "PostgreSQL",
                "server_version": server_version,
                "server_major": int(server_version.split(".", 1)[0]),
                "environment_label": args.environment_label,
            },
            "configuration": {
                "min_size": min_size,
                "max_size": max_size,
                "timeout_seconds": timeout_seconds,
                "max_waiting": max_waiting,
                "connection_check": "ConnectionPool.check_connection",
            },
            "backpressure": {
                "held_connections": max_size,
                "timeout_error_code": "pool_acquisition_timeout",
                "timeout_error_message": timeout_message,
                "timeout_elapsed_seconds": round(timeout_elapsed, 6),
                "dsn_exposed": dsn in timeout_message,
                "queue_full_error_code": "pool_wait_queue_full",
                "queue_full_error_message": queue_full_message,
                "queue_full_dsn_exposed": dsn in queue_full_message,
                "probe_after_release": probe_after_release,
            },
            "connection_replacement": {
                "backend_terminated": terminated,
                "probe_after_termination": probe_after_termination,
                "backend_identity_changed": replacement_pid != original_pid,
                "connections_lost": int(stats.get("connections_lost", 0)),
            },
            "workload": {
                "operation": "SELECT 1 readiness probe",
                "total_operations": args.operations,
                "concurrency": args.concurrency,
                "successes": successes,
                "failures": args.operations - successes,
                "latency_ms": latencies,
                "summary": _latency_summary(latencies),
            },
            "shutdown": {
                "closed": repository.closed,
                "new_work_rejected": new_work_rejected,
                "sessions_before_open": sessions_before,
                "sessions_after_close": sessions_after,
            },
        }
        seal_postgres_pool_artifact(artifact)
    finally:
        if not repository.closed:
            repository.close()
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )

    if artifact is None:
        raise RuntimeError("PostgreSQL pool acceptance did not produce an artifact")
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    summary = artifact["workload"]["summary"]
    print(
        "postgres pool acceptance: "
        f"operations={args.operations} successes={artifact['workload']['successes']} "
        f"p95_ms={summary['p95_ms']:.3f} replacement="
        f"{artifact['connection_replacement']['backend_identity_changed']} "
        f"artifact={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
