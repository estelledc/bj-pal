#!/usr/bin/env python3
"""CLI verifier for an operational alert contract artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.operational_alerts import verify_operational_alert_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    try:
        artifact = verify_operational_alert_artifact(args.artifact)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    metrics = artifact["result"]["metrics"]
    print(f"VALID: {args.artifact}")
    print(
        f"decision={metrics['decision_accuracy_rate']:.3f} "
        f"sample_gate={metrics['sample_gate_accuracy_rate']:.3f} "
        f"source_binding={metrics['source_binding_rate']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
