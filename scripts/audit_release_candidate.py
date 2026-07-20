#!/usr/bin/env python3
"""Generate a deterministic manifest for the exact Git release candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from release_candidate import (  # noqa: E402
    generate_release_candidate_manifest,
    write_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "runtime" / "release_candidate_manifest.json",
    )
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    manifest = generate_release_candidate_manifest(ROOT, base_ref=args.base_ref)
    write_manifest(args.output, manifest)
    print(
        json.dumps(
            {
                "ready": manifest["ready"],
                "candidate_count": manifest["summary"]["candidate_count"],
                "group_counts": manifest["summary"]["group_counts"],
                "violation_count": len(manifest["violations"]),
                "artifact_sha256": manifest["artifact_sha256"],
                "output_name": args.output.name,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if manifest["ready"] or not args.require_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
