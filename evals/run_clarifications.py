#!/usr/bin/env python3
"""Build the durable clarification-continuation evaluation artifact."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.clarifications import (  # noqa: E402
    evaluate_clarification_continuations,
    load_golden_set,
)
from evals.clarifications.verify import canonical_artifact_sha256  # noqa: E402


DEFAULT_GOLDEN = ROOT / "evals" / "clarifications" / "golden.json"
DEFAULT_OUTPUT = ROOT / "evals" / "results" / "clarifications-core.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output_path = args.output if args.output.is_absolute() else ROOT / args.output

    golden = load_golden_set(args.golden)
    result = evaluate_clarification_continuations(golden)
    artifact = {
        "schema_version": 1,
        "evaluation": golden.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": golden.classification,
        "scope_warning": (
            "Metrics cover hand-authored synthetic ambiguity cases and deterministic "
            "preflight continuation. They do not measure open-domain NLU, live planning "
            "quality, or user satisfaction."
        ),
        "golden_set": {
            "sha256": golden.sha256,
            "case_count": len(golden.cases),
            "label_basis": golden.label_basis,
        },
        "result": result,
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    metrics = result["metrics"]
    print(
        "clarification eval: "
        f"cases={result['case_count']} "
        f"one_step={metrics['one_step_resolution_success_rate']:.3f} "
        f"effective={metrics['effective_value_accuracy_rate']:.3f} "
        f"same_conflict={metrics['same_conflict_recurrence_rate']:.3f} "
        f"fingerprint={metrics['decision_fingerprint_valid_rate']:.3f} "
        f"artifact={output_path.relative_to(ROOT)}"
    )
    expected_one = {
        "one_step_resolution_success_rate",
        "effective_value_accuracy_rate",
        "decision_fingerprint_valid_rate",
        "request_fingerprint_valid_rate",
        "continuation_hash_chain_valid_rate",
        "durable_restore_rate",
        "option_contract_coverage_rate",
        "resolution_round_trip_rate",
        "alternate_resolution_conflict_detection_rate",
    }
    if (
        any(metrics[name] != 1 for name in expected_one)
        or metrics["same_conflict_recurrence_rate"] != 0
    ):
        print("clarification continuation missed its synthetic thresholds", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
