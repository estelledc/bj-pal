#!/usr/bin/env python3
"""Independently re-hash the current worktree against a release manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals.release_candidate.verify import verify_release_candidate_manifest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        default=ROOT / "runtime" / "release_candidate_manifest.json",
    )
    args = parser.parse_args()
    artifact = json.loads(args.manifest.read_text(encoding="utf-8"))
    summary = verify_release_candidate_manifest(artifact, ROOT)
    print(f"release-candidate manifest verified: {args.manifest.name}")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
