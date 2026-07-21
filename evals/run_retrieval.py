#!/usr/bin/env python3
"""Run legacy-vs-candidate UGC retrieval evaluation and write raw evidence."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from data_profile import load_data_profile  # noqa: E402
from evals.retrieval import evaluate_retrievers, load_golden_set  # noqa: E402
from evals.retrieval.verify import canonical_artifact_sha256  # noqa: E402
from retrieval import ExplainableUGCRetriever  # noqa: E402
from tools.ugc_bm25 import build_index, search  # noqa: E402


DEFAULT_GOLDEN = ROOT / "evals" / "retrieval" / "golden.json"
DEFAULT_OUTPUT = ROOT / "evals" / "results" / "retrieval-core.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    golden = load_golden_set(args.golden)
    profile = load_data_profile()
    if profile.name != golden.profile or profile.classification != golden.classification:
        raise SystemExit("golden set does not match the active data profile")

    build_index()
    candidate = ExplainableUGCRetriever()

    def legacy(case, top_k):
        return [
            hit.poi_name
            for hit in search(
                case.query,
                top_k=top_k,
                area_anchor=case.area_anchor,
                boost_weekend_afternoon=False,
            )
        ]

    def explainable(case, top_k):
        return [
            hit.poi_name
            for hit in candidate.retrieve(
                case.query,
                top_k=top_k,
                area_anchor=case.area_anchor,
            )
        ]

    results = evaluate_retrievers(
        golden,
        {
            "legacy_bm25": legacy,
            candidate.algorithm: explainable,
        },
    )
    baseline = results["legacy_bm25"]["metrics"]
    current = results[candidate.algorithm]["metrics"]
    hit_key = f"hit_rate_at_{golden.top_k}"
    mrr_key = f"mrr_at_{golden.top_k}"
    recall_key = f"macro_recall_at_{golden.top_k}"
    artifact = {
        "schema_version": 1,
        "evaluation": golden.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_profile": {
            "name": profile.name,
            "classification": profile.classification,
            "public_reproducible": profile.public_reproducible,
        },
        "scope_warning": (
            "Metrics cover a small manually labeled synthetic fixture set; they do not prove "
            "quality on live reviews, other cities, or unconstrained user traffic."
        ),
        "golden_set": {
            "sha256": golden.sha256,
            "case_count": len(golden.cases),
            "top_k": golden.top_k,
            "label_basis": golden.label_basis,
        },
        "results": results,
        "candidate_delta": {
            hit_key: round(current[hit_key] - baseline[hit_key], 6),
            mrr_key: round(current[mrr_key] - baseline[mrr_key], 6),
            recall_key: round(current[recall_key] - baseline[recall_key], 6),
        },
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        "retrieval eval: "
        f"cases={len(golden.cases)} "
        f"legacy_hit@{golden.top_k}={baseline[hit_key]:.3f} "
        f"candidate_hit@{golden.top_k}={current[hit_key]:.3f} "
        f"legacy_mrr@{golden.top_k}={baseline[mrr_key]:.3f} "
        f"candidate_mrr@{golden.top_k}={current[mrr_key]:.3f} "
        f"legacy_recall@{golden.top_k}={baseline[recall_key]:.3f} "
        f"candidate_recall@{golden.top_k}={current[recall_key]:.3f} "
        f"artifact={output_path.relative_to(ROOT)}"
    )
    if (
        current[hit_key] < baseline[hit_key]
        or current[mrr_key] < baseline[mrr_key]
        or current[recall_key] < baseline[recall_key]
    ):
        print("retrieval candidate regressed against the frozen baseline", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
