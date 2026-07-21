#!/usr/bin/env python3
"""CLI verifier for the durable-job diagnostic contract artifact."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.job_diagnostics import verify_job_diagnostic_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    try:
        artifact = verify_job_diagnostic_artifact(args.artifact)
    except (OSError, ValueError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    metrics = artifact["result"]["metrics"]
    print(f"VALID: {args.artifact}")
    print(json_summary(metrics))
    return 0


def json_summary(metrics: dict) -> str:
    return (
        f"classes={metrics['classification_coverage_count']} "
        f"classification={metrics['classification_accuracy_rate']:.3f} "
        f"action={metrics['recommended_action_accuracy_rate']:.3f} "
        f"privacy={metrics['privacy_minimization_rate']:.3f}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
