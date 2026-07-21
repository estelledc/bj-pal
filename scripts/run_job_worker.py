"""Claim and execute one durable planning job."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs import PlanningJobService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id", default="bj-pal-worker")
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=None)
    args = parser.parse_args()

    service = PlanningJobService()
    try:
        job = service.run_once(
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
            heartbeat_interval_seconds=args.heartbeat_interval_seconds,
        )
    finally:
        service.close()
    if job is None:
        print(json.dumps({"status": "idle"}))
        return 0
    print(
        json.dumps(
            {
                "job_id": job.job_id,
                "status": job.status,
                "attempt": job.attempt,
                "deadline_at": job.deadline_at,
                "artifact_id": job.artifact_id,
                "artifact_sha256": job.artifact_sha256,
                "error_code": job.error_code,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if job.status in {"succeeded", "queued", "cancelled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
