#!/usr/bin/env python3
"""Run a bounded HTTP benchmark through a real loopback TCP socket.

The server is a separate Uvicorn subprocess bound to ``127.0.0.1``. Runtime
state is redirected to a temporary directory, configured model credentials are
removed from the child environment, and the process is always waited before an
artifact can be accepted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from data_profile import load_data_profile  # noqa: E402
from evals.performance import seal_performance_artifact  # noqa: E402
from evals.run_http_benchmark import (  # noqa: E402
    PAYLOAD,
    _canonical_sha256,
    _round,
    _response_error_code,
    _summarize,
)


DEFAULT_OUTPUT = ROOT / "evals" / "results" / "socket-http-performance.json"
REQUEST_ID_PREFIX = "socket-bench-"
LOOPBACK_HOST = "127.0.0.1"
_SECRET_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "DPSK_API_KEY",
    "LONGCAT_API_KEY",
    "BJ_PAL_CONTROL_TOKEN",
    "BJ_PAL_CONTROL_PRINCIPALS_JSON",
}


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK_HOST, 0))
        return int(probe.getsockname()[1])


def _server_environment(runtime_dir: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for key in _SECRET_ENV_KEYS:
        environment.pop(key, None)
    environment.update(
        {
            "BJ_PAL_LLM": "mock",
            "BJ_PAL_ENV_FILE": str(runtime_dir / "disabled.env"),
            "BJ_PAL_FEEDBACK_DB": str(runtime_dir / "feedback.db"),
            "BJ_PAL_JOB_DB": str(runtime_dir / "jobs.db"),
            "BJ_PAL_CLARIFICATION_DB": str(runtime_dir / "clarifications.db"),
            "BJ_PAL_TOOL_AUDIT_DB": str(runtime_dir / "tool-audit.db"),
            "BJ_PAL_PLAN_EVIDENCE_DB": str(runtime_dir / "plan-evidence.db"),
            "BJ_PAL_USER_MEMORY_DB": str(runtime_dir / "user-memory.db"),
            "BJ_PAL_PREDICTION_DB": str(runtime_dir / "prediction-feedback.db"),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


async def _wait_until_ready(
    client: httpx.AsyncClient,
    process: subprocess.Popen[str],
    *,
    timeout_seconds: float,
) -> tuple[int, str, float]:
    started = time.perf_counter()
    deadline = started + timeout_seconds
    while time.perf_counter() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Uvicorn exited before readiness")
        try:
            response = await client.get("/readyz")
            body = response.json()
            body_status = body.get("status") if isinstance(body, dict) else None
            if response.status_code == 200 and body_status == "ready":
                return response.status_code, body_status, _round(
                    (time.perf_counter() - started) * 1000
                )
        except (httpx.HTTPError, json.JSONDecodeError):
            pass
        await asyncio.sleep(0.05)
    raise TimeoutError("Uvicorn readiness probe timed out")


async def _exercise_server(
    *,
    process: subprocess.Popen[str],
    port: int,
    total_requests: int,
    concurrency: int,
    warmup_requests: int,
    timeout_seconds: float,
    startup_timeout_seconds: float,
) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        base_url=f"http://{LOOPBACK_HOST}:{port}",
        timeout=timeout_seconds,
        limits=limits,
        trust_env=False,
    ) as client:
        probe_code, probe_status, startup_ms = await _wait_until_ready(
            client,
            process,
            timeout_seconds=startup_timeout_seconds,
        )
        for index in range(warmup_requests):
            response = await client.post(
                "/v1/plans",
                headers={"X-Request-ID": f"socket-warmup-{index:04d}"},
                json=PAYLOAD,
            )
            response.raise_for_status()

        async def one(index: int) -> dict[str, Any]:
            request_id = f"{REQUEST_ID_PREFIX}{index:04d}"
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.post(
                        "/v1/plans",
                        headers={"X-Request-ID": request_id},
                        json=PAYLOAD,
                    )
                    return {
                        "request_index": index,
                        "request_id": request_id,
                        "status_code": response.status_code,
                        "echoed_request_id": response.headers.get("X-Request-ID"),
                        "latency_ms": _round((time.perf_counter() - started) * 1000),
                        "error_code": _response_error_code(response),
                    }
                except Exception as exc:  # retain only a safe failure class
                    return {
                        "request_index": index,
                        "request_id": request_id,
                        "status_code": 0,
                        "echoed_request_id": None,
                        "latency_ms": _round((time.perf_counter() - started) * 1000),
                        "error_code": type(exc).__name__,
                    }

        wall_started = time.perf_counter()
        raw_requests = await asyncio.gather(
            *(one(index) for index in range(total_requests))
        )
        wall_seconds = _round(time.perf_counter() - wall_started)
    return raw_requests, wall_seconds, {
        "kind": "uvicorn_subprocess",
        "bind_host": LOOPBACK_HOST,
        "network_scope": "ipv4_loopback_only",
        "startup_probe_endpoint": "/readyz",
        "startup_probe_status_code": probe_code,
        "startup_probe_body_status": probe_status,
        "startup_ms": startup_ms,
    }


def _stop_server(process: subprocess.Popen[str]) -> tuple[str, int]:
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            return "sigint_and_wait", process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            return "kill_after_timeout", process.wait(timeout=5)
    return "already_exited", int(process.returncode or 0)


async def run_socket_benchmark(
    *,
    total_requests: int,
    concurrency: int,
    warmup_requests: int,
    timeout_seconds: float,
    startup_timeout_seconds: float,
) -> tuple[list[dict[str, Any]], float, dict[str, Any], str]:
    with TemporaryDirectory(prefix="bj-pal-socket-benchmark-") as temporary:
        runtime_dir = Path(temporary)
        port = _reserve_loopback_port()
        log_path = runtime_dir / "uvicorn.log"
        with log_path.open("w+", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "http_api.app:app",
                    "--app-dir",
                    str(ROOT / "src"),
                    "--host",
                    LOOPBACK_HOST,
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=_server_environment(runtime_dir),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                raw_requests, wall_seconds, server_process = await _exercise_server(
                    process=process,
                    port=port,
                    total_requests=total_requests,
                    concurrency=concurrency,
                    warmup_requests=warmup_requests,
                    timeout_seconds=timeout_seconds,
                    startup_timeout_seconds=startup_timeout_seconds,
                )
            except Exception:
                _stop_server(process)
                log.flush()
                log.seek(0)
                tail = "".join(log.readlines()[-20:]).strip()
                if tail:
                    print(f"Uvicorn diagnostic tail:\n{tail}", file=sys.stderr)
                raise
            shutdown_method, shutdown_exit_code = _stop_server(process)
            server_process.update(
                {
                    "shutdown_method": shutdown_method,
                    "shutdown_exit_code": shutdown_exit_code,
                }
            )
        return raw_requests, wall_seconds, server_process, "temporary_isolated_runtime"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--startup-timeout", type=float, default=20.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not 1 <= args.requests <= 10_000:
        parser.error("--requests must be between 1 and 10000")
    if not 1 <= args.concurrency <= args.requests:
        parser.error("--concurrency must be between 1 and --requests")
    if not 0 <= args.warmup <= 1_000:
        parser.error("--warmup must be between 0 and 1000")
    if not 0 < args.timeout <= 600:
        parser.error("--timeout must be between 0 and 600 seconds")
    if not 0 < args.startup_timeout <= 120:
        parser.error("--startup-timeout must be between 0 and 120 seconds")
    return args


def main() -> int:
    args = parse_args()
    profile = load_data_profile()
    if not profile.public_reproducible:
        raise SystemExit("benchmark requires `make bootstrap-demo` and a reproducible profile")

    raw_requests, wall_seconds, server_process, runtime_isolation = asyncio.run(
        run_socket_benchmark(
            total_requests=args.requests,
            concurrency=args.concurrency,
            warmup_requests=args.warmup,
            timeout_seconds=args.timeout,
            startup_timeout_seconds=args.startup_timeout,
        )
    )
    summary = _summarize(raw_requests, wall_seconds)
    artifact = {
        "schema_id": "bj-pal.http-performance-artifact",
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope_warning": (
            "This is one localhost-only Uvicorn subprocess with a mock LLM and synthetic "
            "data. It exercises a real TCP socket and process boundary, but excludes remote "
            "networks, TLS/proxy overhead, real-model latency, multi-instance contention, "
            "and production traffic; it is acceptance evidence, not an SLA."
        ),
        "run": {
            "transport": "localhost_tcp",
            "backend": "mock",
            "runtime_isolation": runtime_isolation,
            "server_process": server_process,
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
            },
            "data_profile": {
                "name": profile.name,
                "classification": profile.classification,
                "public_reproducible": profile.public_reproducible,
            },
        },
        "workload": {
            "method": "POST",
            "endpoint": "/v1/plans",
            "total_requests": args.requests,
            "concurrency": args.concurrency,
            "warmup_requests": args.warmup,
            "timeout_seconds": args.timeout,
            "latency_scope": "request_after_semaphore_acquire",
            "request_id_prefix": REQUEST_ID_PREFIX,
            "payload": PAYLOAD,
            "payload_sha256": _canonical_sha256(PAYLOAD),
        },
        "raw_requests": raw_requests,
        "measurement": {
            "wall_seconds": wall_seconds,
            "summary": summary,
        },
    }
    seal_performance_artifact(artifact)
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    latency = summary["latency_ms"]
    display_path = (
        output_path.relative_to(ROOT)
        if output_path.is_relative_to(ROOT)
        else output_path
    )
    print(
        "socket http benchmark: "
        f"requests={summary['total_requests']} concurrency={args.concurrency} "
        f"successes={summary['successes']} failures={summary['failures']} "
        f"request_id_mismatches={summary['request_id_mismatches']} "
        f"startup_ms={server_process['startup_ms']:.2f} "
        f"throughput_rps={summary['throughput_rps']:.2f} "
        f"p50_ms={latency['p50']:.2f} p95_ms={latency['p95']:.2f} "
        f"artifact={display_path}"
    )
    clean_server = (
        server_process["shutdown_method"] == "sigint_and_wait"
        and server_process["shutdown_exit_code"] == 0
    )
    return 0 if summary["gate_pass"] and clean_server else 1


if __name__ == "__main__":
    raise SystemExit(main())
