#!/usr/bin/env python3
"""Evaluate the fixed alert policy over two exported, payload-free snapshots."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobs import DurableWorkloadHealth  # noqa: E402
from jobs.workload_health import parse_utc_timestamp  # noqa: E402
from monitoring import OperationalAlertSnapshot  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Combine one integrity-checked workload snapshot and one payload-free "
            "trace-export status into a deterministic operational alert snapshot."
        )
    )
    parser.add_argument("--workload-snapshot", type=Path, required=True)
    parser.add_argument("--trace-status", type=Path, required=True)
    parser.add_argument(
        "--observed-at",
        help="ISO-8601 observation time; defaults to the current UTC time.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _load_object(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return payload


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
    try:
        workload = DurableWorkloadHealth.from_dict(
            _load_object(args.workload_snapshot)
        )
        trace_status = _load_object(args.trace_status)
        observed_at = (
            parse_utc_timestamp(args.observed_at)
            if args.observed_at
            else datetime.now(timezone.utc)
        )
        snapshot = OperationalAlertSnapshot.create(
            workload=workload,
            trace_status=trace_status,
            observed_at=observed_at,
        )
        _write_new(args.output, snapshot.to_dict())
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"operational alert evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "artifact_sha256": snapshot.artifact_sha256,
                "firing_rule_count": snapshot.firing_rule_count,
                "overall_state": snapshot.overall_state,
                "output_name": args.output.name,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
