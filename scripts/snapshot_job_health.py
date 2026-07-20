#!/usr/bin/env python3
"""Build one bounded, tenant-scoped durable workload health snapshot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobs import (  # noqa: E402
    JobWorkloadEvidenceLimitExceeded,
    PlanningJobRepository,
    PlanningJobService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate one exact durable-job window without emitting tenant, principal, "
            "request, job IDs, worker IDs, event payloads, or error messages."
        )
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument(
        "--database",
        type=Path,
        help="Explicit planning-job SQLite path; otherwise the configured default is used.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Create a new mode-0600 JSON artifact instead of printing the full snapshot.",
    )
    return parser


def _write_new(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise


def main() -> int:
    args = _parser().parse_args()
    repository = (
        PlanningJobRepository(args.database)
        if args.database is not None
        else PlanningJobRepository()
    )
    service = PlanningJobService(repository=repository)
    try:
        snapshot = service.workload_health(
            tenant_id=args.tenant_id,
            window_start=args.window_start,
            window_end=args.window_end,
        )
    except (JobWorkloadEvidenceLimitExceeded, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    payload = snapshot.to_dict()
    if args.output is None:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _write_new(args.output, payload)
    print(
        json.dumps(
            {
                "artifact_sha256": snapshot.artifact_sha256,
                "job_count": snapshot.job_count,
                "output_name": args.output.name,
                "window_end": snapshot.window_end,
                "window_start": snapshot.window_start,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
