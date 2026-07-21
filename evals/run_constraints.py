#!/usr/bin/env python3
"""Run the deterministic Constraint Ledger against its synthetic golden set."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.constraints import evaluate_constraint_ledger, load_golden_set  # noqa: E402
from evals.constraints.verify import canonical_artifact_sha256  # noqa: E402


DEFAULT_GOLDEN = ROOT / "evals" / "constraints" / "golden.json"
DEFAULT_OUTPUT = ROOT / "evals" / "results" / "constraints-core.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output_path = args.output if args.output.is_absolute() else ROOT / args.output

    golden = load_golden_set(args.golden)
    result = evaluate_constraint_ledger(golden)
    artifact = {
        "schema_version": 1,
        "evaluation": golden.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": golden.classification,
        "scope_warning": (
            "Metrics cover hand-authored synthetic phrases and supported typed fields. "
            "They do not establish open-domain Mandarin understanding or live user satisfaction."
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
        "constraint eval: "
        f"cases={result['case_count']} "
        f"field_f1={metrics['field_extraction_f1']:.3f} "
        f"false_extraction={metrics['false_extraction_rate']:.3f} "
        f"preservation={metrics['hard_constraint_preservation_rate']:.3f} "
        f"conflict_recall={metrics['explicit_conflict_detection_recall']:.3f} "
        f"idempotency={metrics['round_trip_idempotency_rate']:.3f} "
        f"artifact={output_path.relative_to(ROOT)}"
    )
    if any(
        metrics[name] != expected
        for name, expected in {
            "field_extraction_precision": 1,
            "field_extraction_recall": 1,
            "field_extraction_f1": 1,
            "false_extraction_rate": 0,
            "hard_constraint_preservation_rate": 1,
            "explicit_conflict_detection_recall": 1,
            "false_conflict_rate": 0,
            "rewrite_constraint_coverage_rate": 1,
            "round_trip_idempotency_rate": 1,
        }.items()
    ):
        print("constraint ledger missed its synthetic acceptance thresholds", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
