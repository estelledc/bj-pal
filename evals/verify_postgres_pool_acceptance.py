#!/usr/bin/env python3
"""Verify a checked-in PostgreSQL pool acceptance artifact offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.postgres_pool import (  # noqa: E402
    PostgresPoolArtifactError,
    verify_postgres_pool_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    path = args.artifact if args.artifact.is_absolute() else ROOT / args.artifact
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        summary = verify_postgres_pool_artifact(artifact)
    except (OSError, json.JSONDecodeError, PostgresPoolArtifactError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {path}")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
