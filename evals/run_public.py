"""Run the full deterministic public suite and emit one verifiable artifact."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.artifacts import build_artifact, verify_artifact, write_artifact  # noqa: E402
from evals.behavioral.run_l1 import run_all as run_l1  # noqa: E402
from evals.behavioral.run_l2 import run_all as run_l2  # noqa: E402
from evals.behavioral.run_l3 import run_all as run_l3  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evals" / "results" / "public-core.json",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    os.environ["BJ_PAL_LLM"] = "mock"
    reports = {
        "L1": run_l1(verbose=args.verbose),
        "L2": run_l2(verbose=args.verbose),
        "L3": run_l3(verbose=args.verbose),
    }
    artifact = build_artifact(reports)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    write_artifact(artifact, output)
    summary = verify_artifact(artifact)

    print(f"artifact: {output}")
    for level, result in summary["levels"].items():
        passed = result.get("n_pass", result.get("n_all_pass"))
        print(f"{level}: {passed}/{result['n_cases']} gate_pass={result['gate_pass']}")
    print(f"payload_sha256: {artifact['integrity']['payload_sha256']}")
    print(f"semantic_sha256: {artifact['integrity']['semantic_sha256']}")
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
