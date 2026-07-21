#!/usr/bin/env python3
"""Run a bounded in-process ASGI benchmark and retain per-request evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
os.environ["BJ_PAL_LLM"] = "mock"

import httpx  # noqa: E402

from data_profile import load_data_profile  # noqa: E402
from evals.performance import seal_performance_artifact  # noqa: E402
from outcomes import PlanFeedbackRepository, PlanFeedbackService  # noqa: E402


DEFAULT_OUTPUT = ROOT / "evals" / "results" / "http-performance.json"
REQUEST_ID_PREFIX = "bench-"
PAYLOAD = {
    "user_input": "周末下午带 5 岁孩子在五道营附近玩四小时，不吃辣",
    "persona": "family",
    "preferences": {
        "party_size": 3,
        "has_child": True,
        "child_age": 5,
        "diet_flags": ["no_spicy"],
        "duration_hours": 4,
    },
}


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _nearest_rank(values: Sequence[float], percentile: int) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def _round(value: float) -> float:
    return round(value, 6)


def _response_error_code(response: httpx.Response) -> str | None:
    if response.status_code == 200:
        return None
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return f"http_{response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return f"http_{response.status_code}"


def _summarize(raw_requests: list[dict[str, Any]], wall_seconds: float) -> dict[str, Any]:
    latencies = [float(item["latency_ms"]) for item in raw_requests]
    successes = sum(
        item["status_code"] == 200 and item["error_code"] is None
        for item in raw_requests
    )
    mismatches = sum(
        item["echoed_request_id"] != item["request_id"] for item in raw_requests
    )
    failures = len(raw_requests) - successes
    return {
        "total_requests": len(raw_requests),
        "successes": successes,
        "failures": failures,
        "request_id_mismatches": mismatches,
        "error_rate": _round(failures / len(raw_requests)),
        "throughput_rps": _round(len(raw_requests) / wall_seconds),
        "latency_ms": {
            "method": "nearest_rank",
            "min": _round(min(latencies)),
            "p50": _round(_nearest_rank(latencies, 50)),
            "p95": _round(_nearest_rank(latencies, 95)),
            "p99": _round(_nearest_rank(latencies, 99)),
            "max": _round(max(latencies)),
        },
        "gate_pass": failures == 0 and mismatches == 0,
    }


async def run_benchmark(
    *,
    total_requests: int,
    concurrency: int,
    warmup_requests: int,
    timeout_seconds: float,
) -> tuple[list[dict[str, Any]], float]:
    temporary = TemporaryDirectory(prefix="bj-pal-http-benchmark-")
    isolated_state = {
        "BJ_PAL_PLAN_EVIDENCE_DB": Path(temporary.name) / "plan-evidence.db",
        "BJ_PAL_USER_MEMORY_DB": Path(temporary.name) / "user-memory.db",
        "BJ_PAL_PREDICTION_DB": Path(temporary.name) / "prediction-feedback.db",
    }
    previous_state = {key: os.environ.get(key) for key in isolated_state}
    for key, path in isolated_state.items():
        os.environ[key] = str(path)
    try:
        from http_api.app import create_app

        feedback_service = PlanFeedbackService(
            PlanFeedbackRepository(Path(temporary.name) / "feedback.db")
        )
        app = create_app(feedback_service=feedback_service)
        transport = httpx.ASGITransport(app=app)
        semaphore = asyncio.Semaphore(concurrency)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://benchmark.local",
            timeout=timeout_seconds,
        ) as client:
            for index in range(warmup_requests):
                response = await client.post(
                    "/v1/plans",
                    headers={"X-Request-ID": f"warmup-{index:04d}"},
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
                            "latency_ms": _round(
                                (time.perf_counter() - started) * 1000
                            ),
                            "error_code": _response_error_code(response),
                        }
                    except Exception as exc:  # retain class without response body
                        return {
                            "request_index": index,
                            "request_id": request_id,
                            "status_code": 0,
                            "echoed_request_id": None,
                            "latency_ms": _round(
                                (time.perf_counter() - started) * 1000
                            ),
                            "error_code": type(exc).__name__,
                        }

            wall_started = time.perf_counter()
            raw_requests = await asyncio.gather(
                *(one(index) for index in range(total_requests))
            )
            wall_seconds = _round(time.perf_counter() - wall_started)
        return raw_requests, wall_seconds
    finally:
        for key, previous in previous_state.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        temporary.cleanup()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=60.0)
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
    return args


def main() -> int:
    args = parse_args()
    profile = load_data_profile()
    if not profile.public_reproducible:
        raise SystemExit("benchmark requires `make bootstrap-demo` and a reproducible profile")

    raw_requests, wall_seconds = asyncio.run(
        run_benchmark(
            total_requests=args.requests,
            concurrency=args.concurrency,
            warmup_requests=args.warmup,
            timeout_seconds=args.timeout,
        )
    )
    summary = _summarize(raw_requests, wall_seconds)
    artifact = {
        "schema_id": "bj-pal.http-performance-artifact",
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope_warning": (
            "This is a single-process, in-process ASGI run with a mock LLM and synthetic "
            "data. It excludes network, process startup, real-model latency, multi-instance "
            "contention, and production traffic; it is regression evidence, not an SLA."
        ),
        "run": {
            "transport": "in_process_asgi",
            "backend": "mock",
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
        "http benchmark: "
        f"requests={summary['total_requests']} concurrency={args.concurrency} "
        f"successes={summary['successes']} failures={summary['failures']} "
        f"request_id_mismatches={summary['request_id_mismatches']} "
        f"throughput_rps={summary['throughput_rps']:.2f} "
        f"p50_ms={latency['p50']:.2f} p95_ms={latency['p95']:.2f} "
        f"artifact={display_path}"
    )
    return 0 if summary["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
