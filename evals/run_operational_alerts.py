#!/usr/bin/env python3
"""Build the deterministic operational alert contract artifact."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.operational_alerts import (  # noqa: E402
    canonical_artifact_sha256,
    evaluate_operational_alerts,
)


DEFAULT_OUTPUT = ROOT / "evals" / "results" / "operational-alert-contract.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    result = evaluate_operational_alerts()
    artifact = {
        "schema_version": 1,
        "evaluation": "operational-alert-contract",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": "deterministic_synthetic_contract",
        "scope_warning": (
            "Four authored synthetic cases prove fixed rule decisions, minimum-sample "
            "gates, source binding, integrity, and privacy boundaries. They do not prove "
            "production SLOs, alert delivery, incident response, capacity, or users."
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
        "operational alert artifact: "
        f"cases={result['case_count']} "
        f"decision={metrics['decision_accuracy_rate']:.3f} "
        f"sample_gate={metrics['sample_gate_accuracy_rate']:.3f} "
        f"source_binding={metrics['source_binding_rate']:.3f} "
        f"artifact={display}"
    )
    if any(value != 1 for value in metrics.values()):
        print("operational alert contract missed its thresholds", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
