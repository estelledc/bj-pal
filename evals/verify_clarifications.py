#!/usr/bin/env python3
"""CLI verifier for a clarification-continuation artifact."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.clarifications.verify import verify_clarification_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--golden",
        type=Path,
        default=ROOT / "evals" / "clarifications" / "golden.json",
    )
    args = parser.parse_args()
    try:
        verify_clarification_artifact(args.artifact, args.golden)
    except (OSError, ValueError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {args.artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
