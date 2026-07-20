from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evals.side_effects.evaluate import evaluate_side_effects, write_artifact  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evals/results/side-effect-safety.json",
    )
    args = parser.parse_args()
    artifact = evaluate_side_effects()
    write_artifact(args.output, artifact)
    print(f"side-effect artifact: {args.output}")
    print(artifact["result"]["metrics"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
