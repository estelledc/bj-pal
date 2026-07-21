from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals.user_memory_state.verify import verify_user_memory_state  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    metrics = verify_user_memory_state(
        json.loads(args.artifact.read_text(encoding="utf-8"))
    )
    print(f"user-memory-state artifact verified: {args.artifact}")
    print(metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
