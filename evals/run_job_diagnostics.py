#!/usr/bin/env python3
"""Build the synthetic durable-job diagnostic contract artifact."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.job_diagnostics import (  # noqa: E402
    canonical_artifact_sha256,
    evaluate_job_diagnostics,
)


DEFAULT_OUTPUT = ROOT / "evals" / "results" / "job-diagnostics-contract.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    result = evaluate_job_diagnostics()
    artifact = {
        "schema_version": 1,
        "evaluation": "durable-job-incident-diagnosis",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": "deterministic_synthetic_contract",
        "scope_warning": (
            "Fixed synthetic state/event chains prove bounded classification, hashing, "
            "tenant-facing response shape, and privacy minimization. They do not prove "
            "the underlying provider or worker root cause, production incident coverage, "
            "alert quality, or operational recovery success."
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
    display_output = output.relative_to(ROOT) if output.is_relative_to(ROOT) else output
    print(
        "job diagnostics artifact: "
        f"cases={result['case_count']} "
        f"classes={metrics['classification_coverage_count']} "
        f"classification={metrics['classification_accuracy_rate']:.3f} "
        f"action={metrics['recommended_action_accuracy_rate']:.3f} "
        f"privacy={metrics['privacy_minimization_rate']:.3f} "
        f"artifact={display_output}"
    )
    if (
        metrics["classification_accuracy_rate"] != 1
        or metrics["recommended_action_accuracy_rate"] != 1
        or metrics["classification_coverage_count"] != 14
        or metrics["integrity_rate"] != 1
        or metrics["privacy_minimization_rate"] != 1
        or metrics["unknown_error_non_promotion_rate"] != 1
    ):
        print("job diagnostic contract missed its synthetic thresholds", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
