#!/usr/bin/env python3
"""CLI verifier for a durable workload health artifact."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.workload_health import verify_workload_health_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    try:
        artifact = verify_workload_health_artifact(args.artifact)
    except (OSError, ValueError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    metrics = artifact["result"]["metrics"]
    print(f"VALID: {args.artifact}")
    print(
        f"aggregate={metrics['aggregate_accuracy_rate']:.3f} "
        f"latency={metrics['latency_accuracy_rate']:.3f} "
        f"privacy={metrics['privacy_minimization_rate']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
