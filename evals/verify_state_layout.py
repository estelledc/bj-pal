#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.state_layout.verify import verify_state_layout_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    artifact = verify_state_layout_artifact(args.artifact)
    print(f"state-layout artifact verified: {args.artifact}")
    print(artifact["result"]["metrics"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
