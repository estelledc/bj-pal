"""Verify a public evaluation artifact without rerunning the product."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.artifacts import (  # noqa: E402
    ArtifactVerificationError,
    read_artifact,
    verify_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    path = args.artifact if args.artifact.is_absolute() else ROOT / args.artifact
    try:
        summary = verify_artifact(read_artifact(path))
    except (ArtifactVerificationError, OSError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {path}")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
