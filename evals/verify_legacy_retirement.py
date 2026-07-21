from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals.legacy_retirement.verify import verify_legacy_retirement  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    metrics = verify_legacy_retirement(
        json.loads(args.artifact.read_text(encoding="utf-8"))
    )
    print(f"legacy-retirement artifact verified: {args.artifact}")
    print(metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
