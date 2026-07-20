#!/usr/bin/env python3
"""Build the deterministic durable workload health contract artifact."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.workload_health import (  # noqa: E402
    canonical_artifact_sha256,
    evaluate_workload_health,
)


DEFAULT_OUTPUT = ROOT / "evals" / "results" / "workload-health-contract.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    result = evaluate_workload_health()
    artifact = {
        "schema_version": 1,
        "evaluation": "durable-workload-health",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": "deterministic_synthetic_contract",
        "scope_warning": (
            "Two fixed synthetic windows prove aggregate definitions, nearest-rank "
            "latencies, empty-window semantics, hashing, and privacy minimization. "
            "They do not prove production SLOs, incident frequency, capacity, or users."
        ),
        "result": result,
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    metrics = result["metrics"]
    display = output.relative_to(ROOT) if output.is_relative_to(ROOT) else output
    print(
        "workload health artifact: "
        f"cases={result['case_count']} "
        f"aggregate={metrics['aggregate_accuracy_rate']:.3f} "
        f"latency={metrics['latency_accuracy_rate']:.3f} "
        f"privacy={metrics['privacy_minimization_rate']:.3f} "
        f"artifact={display}"
    )
    if any(
        metrics[key] != 1
        for key in (
            "aggregate_accuracy_rate",
            "latency_accuracy_rate",
            "integrity_rate",
            "privacy_minimization_rate",
            "empty_window_null_rate",
        )
    ):
        print("workload health contract missed its thresholds", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
