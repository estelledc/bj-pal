from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evals.legacy_retirement.evaluate import (  # noqa: E402
    evaluate_legacy_retirement,
    write_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifact = evaluate_legacy_retirement()
    write_artifact(args.output, artifact)
    print(f"legacy-retirement artifact: {args.output}")
    print(artifact["result"]["metrics"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
