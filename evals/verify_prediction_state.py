from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals.prediction_state.verify import verify_prediction_state  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    metrics = verify_prediction_state(json.loads(args.artifact.read_text()))
    print(f"prediction-state artifact verified: {args.artifact}")
    print(metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
