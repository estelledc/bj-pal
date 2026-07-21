from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.retrieval import evaluate_retrievers, load_golden_set  # noqa: E402
from evals.retrieval.verify import (  # noqa: E402
    canonical_artifact_sha256,
    verify_retrieval_artifact,
)


def test_metric_calculation_keeps_raw_case_evidence(tmp_path) -> None:
    path = tmp_path / "golden.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "fixture",
                "profile": "demo",
                "classification": "synthetic",
                "label_basis": "test",
                "top_k": 2,
                "cases": [
                    {
                        "case_id": "a",
                        "query": "qa",
                        "area_anchor": "area",
                        "relevant_poi_names": ["target-a"],
                    },
                    {
                        "case_id": "b",
                        "query": "qb",
                        "area_anchor": "area",
                        "relevant_poi_names": ["target-b"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    golden = load_golden_set(path)

    def retrieve(case, top_k):
        assert top_k == 2
        return ["irrelevant", f"target-{case.case_id}"]

    result = evaluate_retrievers(golden, {"fixture": retrieve})["fixture"]
    assert result["metrics"]["hit_rate_at_2"] == 1.0
    assert result["metrics"]["mrr_at_2"] == 0.5
    assert result["metrics"]["macro_recall_at_2"] == 1.0
    assert result["metrics"]["unique_subject_ratio_at_2"] == 1.0
    assert result["raw_cases"][0]["first_relevant_rank"] == 2
    assert len(golden.sha256) == 64


def test_project_golden_set_is_nontrivial_and_scoped() -> None:
    golden = load_golden_set(ROOT / "evals" / "retrieval" / "golden.json")
    assert len(golden.cases) >= 18
    assert golden.profile == "demo"
    assert golden.classification == "synthetic"
    assert "POI level" in golden.label_basis


def test_verifier_recomputes_metrics_even_after_hash_is_rewritten(tmp_path) -> None:
    golden_path = tmp_path / "golden.json"
    golden_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "fixture",
                "profile": "demo",
                "classification": "synthetic",
                "label_basis": "fixture",
                "top_k": 2,
                "cases": [
                    {
                        "case_id": "a",
                        "query": "query",
                        "area_anchor": "area",
                        "relevant_poi_names": ["target"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    golden = load_golden_set(golden_path)

    def hit(*args):
        del args
        return ["target", "other"]

    results = evaluate_retrievers(golden, {"legacy_bm25": hit, "candidate": hit})
    artifact = {
        "schema_version": 1,
        "golden_set": {"sha256": golden.sha256, "case_count": 1, "top_k": 2},
        "results": results,
        "candidate_delta": {
            "hit_rate_at_2": 0.0,
            "mrr_at_2": 0.0,
            "macro_recall_at_2": 0.0,
        },
    }
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    verify_retrieval_artifact(artifact_path, golden_path)

    artifact["results"]["candidate"]["raw_cases"][0]["returned_poi_names"] = ["other"]
    artifact["artifact_sha256"] = canonical_artifact_sha256(artifact)
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    try:
        verify_retrieval_artifact(artifact_path, golden_path)
    except ValueError as exc:
        assert "rank mismatch" in str(exc)
    else:
        raise AssertionError("semantic tampering should fail verification")
