from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from evals.orchestration.evaluate import evaluate_orchestration, write_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evals/results/orchestration-comparison.json",
    )
    args = parser.parse_args()
    artifact = evaluate_orchestration()
    write_artifact(args.output, artifact)
    print(f"orchestration artifact: {args.output}")
    print(artifact["result"]["metrics"])
    print({"decision": artifact["result"]["decision"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
