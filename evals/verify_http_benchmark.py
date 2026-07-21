#!/usr/bin/env python3
"""Verify a performance artifact without rerunning the application."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.performance.verify import (  # noqa: E402
    PerformanceArtifactError,
    read_performance_artifact,
    verify_performance_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    path = args.artifact if args.artifact.is_absolute() else ROOT / args.artifact
    try:
        summary = verify_performance_artifact(read_performance_artifact(path))
    except (OSError, json.JSONDecodeError, PerformanceArtifactError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {path}")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
