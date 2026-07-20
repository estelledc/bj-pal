from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evals.outcomes.evaluate import evaluate_outcomes, write_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evals/results/human-outcome-contract.json",
    )
    args = parser.parse_args()
    artifact = evaluate_outcomes()
    write_artifact(args.output, artifact)
    print(f"human-outcome artifact: {args.output}")
    print(artifact["result"]["metrics"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
