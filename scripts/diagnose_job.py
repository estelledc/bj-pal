#!/usr/bin/env python3
"""Build a bounded, privacy-minimized diagnosis for one durable planning job."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobs import (  # noqa: E402
    JobDiagnosticEventLimitExceeded,
    PlanningJobRepository,
    PlanningJobService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Classify one durable job from its current state and append-only events. "
            "The output excludes request payloads, tenant/principal IDs, worker IDs, "
            "provider error text, and event payloads."
        )
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument(
        "--database",
        type=Path,
        help="Explicit planning-job SQLite path; otherwise the configured default is used.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Create a new mode-0600 JSON artifact instead of printing the full artifact.",
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
        diagnosis = service.diagnose(args.job_id, tenant_id=args.tenant_id)
    except JobDiagnosticEventLimitExceeded as exc:
        raise SystemExit(str(exc)) from exc
    if diagnosis is None:
        raise SystemExit("planning job was not found in the requested tenant")
    payload = diagnosis.to_dict()
    if args.output is None:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _write_new(args.output, payload)
    print(
        json.dumps(
            {
                "artifact_sha256": diagnosis.artifact_sha256,
                "classification": diagnosis.classification,
                "output_name": args.output.name,
                "status": diagnosis.status,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
