from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals.prediction_state.evaluate import evaluate_prediction_state, write_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifact = evaluate_prediction_state()
    write_artifact(args.output, artifact)
    print(f"prediction-state artifact: {args.output}")
    print(artifact["result"]["metrics"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
