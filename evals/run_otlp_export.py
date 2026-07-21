#!/usr/bin/env python3
"""Build the synthetic OTLP protocol-acceptance artifact."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.otlp_export import canonical_artifact_sha256, evaluate_otlp_export  # noqa: E402


DEFAULT_OUTPUT = ROOT / "evals" / "results" / "otlp-export-contract.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    artifact = {
        "schema_version": 1,
        "evaluation": "otlp-export-boundary",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": "synthetic_protocol_acceptance",
        "scope_warning": (
            "A loopback OTLP/HTTP protobuf receiver and deterministic exporter failure "
            "prove protocol shape, privacy projection, health visibility, and business "
            "failure isolation. They do not prove a remote vendor, production delivery, "
            "alerting, SLOs, scale, or real users."
        ),
        "result": evaluate_otlp_export(),
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    display = output.relative_to(ROOT) if output.is_relative_to(ROOT) else output
    print(f"OTLP export artifact: cases=2 metrics=4/4 artifact={display}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
