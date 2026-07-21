#!/usr/bin/env python3
"""CLI verifier for the OTLP protocol-acceptance artifact."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.otlp_export import verify_otlp_export_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    try:
        artifact = verify_otlp_export_artifact(args.artifact)
    except (OSError, ValueError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    metrics = artifact["result"]["metrics"]
    print(f"VALID: {args.artifact}")
    print(" ".join(f"{key}={value}" for key, value in metrics.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
