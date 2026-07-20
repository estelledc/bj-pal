from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.scheduling.verify import verify_scheduling_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    artifact = verify_scheduling_artifact(args.artifact)
    print(f"scheduling artifact verified: {args.artifact}")
    print(artifact["result"]["metrics"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
