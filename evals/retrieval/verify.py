"""Independent integrity and semantic verification for retrieval artifacts."""

from __future__ import annotations

import hashlib
import json
import statistics
from copy import deepcopy
from pathlib import Path

from .evaluate import GoldenSet, load_golden_set


def canonical_artifact_sha256(payload: dict) -> str:
    canonical_payload = deepcopy(payload)
    canonical_payload.pop("artifact_sha256", None)
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_retrieval_artifact(artifact_path: Path, golden_path: Path) -> dict:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported retrieval artifact schema")
    if artifact.get("artifact_sha256") != canonical_artifact_sha256(artifact):
        raise ValueError("retrieval artifact SHA-256 mismatch")

    golden = load_golden_set(golden_path)
    recorded_golden = artifact.get("golden_set") or {}
    if recorded_golden.get("sha256") != golden.sha256:
        raise ValueError("retrieval golden-set SHA-256 mismatch")
    if recorded_golden.get("case_count") != len(golden.cases):
        raise ValueError("retrieval golden-set case count mismatch")
    if recorded_golden.get("top_k") != golden.top_k:
        raise ValueError("retrieval top_k mismatch")

    results = artifact.get("results") or {}
    if "legacy_bm25" not in results or len(results) != 2:
        raise ValueError("retrieval artifact must contain one baseline and one candidate")

    recomputed = {
        name: _verify_and_recompute_result(golden, result)
        for name, result in results.items()
    }
    candidate_name = next(name for name in results if name != "legacy_bm25")
    hit_key = f"hit_rate_at_{golden.top_k}"
    mrr_key = f"mrr_at_{golden.top_k}"
    recall_key = f"macro_recall_at_{golden.top_k}"
    expected_delta = {
        key: round(
            recomputed[candidate_name][key] - recomputed["legacy_bm25"][key],
            6,
        )
        for key in (hit_key, mrr_key, recall_key)
    }
    if artifact.get("candidate_delta") != expected_delta:
        raise ValueError("retrieval candidate delta does not match raw cases")
    return artifact


def _verify_and_recompute_result(golden: GoldenSet, result: dict) -> dict:
    raw_cases = result.get("raw_cases") or []
    if result.get("case_count") != len(golden.cases) or len(raw_cases) != len(golden.cases):
        raise ValueError("retrieval result case count mismatch")

    by_id = {case["case_id"]: case for case in raw_cases}
    if len(by_id) != len(raw_cases):
        raise ValueError("retrieval raw case IDs must be unique")

    hits = 0
    reciprocal_ranks: list[float] = []
    recalls: list[float] = []
    unique_ratios: list[float] = []
    for expected in golden.cases:
        observed = by_id.get(expected.case_id)
        if observed is None:
            raise ValueError(f"missing retrieval raw case: {expected.case_id}")
        if (
            observed.get("query") != expected.query
            or observed.get("area_anchor") != expected.area_anchor
            or observed.get("relevant_poi_names") != list(expected.relevant_poi_names)
        ):
            raise ValueError(f"retrieval case contract mismatch: {expected.case_id}")

        returned = list(observed.get("returned_poi_names") or [])[: golden.top_k]
        relevant = set(expected.relevant_poi_names)
        rank = next(
            (index for index, subject in enumerate(returned, start=1) if subject in relevant),
            None,
        )
        found = sorted(relevant.intersection(returned))
        recall = len(found) / len(relevant)
        if observed.get("first_relevant_rank") != rank:
            raise ValueError(f"retrieval rank mismatch: {expected.case_id}")
        if observed.get("found_relevant_poi_names") != found:
            raise ValueError(f"retrieval relevant-subject mismatch: {expected.case_id}")
        if observed.get(f"recall_at_{golden.top_k}") != round(recall, 6):
            raise ValueError(f"retrieval recall mismatch: {expected.case_id}")

        hits += int(rank is not None)
        reciprocal_ranks.append(1.0 / rank if rank is not None else 0.0)
        recalls.append(recall)
        unique_ratios.append(len(set(returned)) / len(returned) if returned else 1.0)

    metrics = {
        f"hit_rate_at_{golden.top_k}": round(hits / len(golden.cases), 6),
        f"mrr_at_{golden.top_k}": round(statistics.fmean(reciprocal_ranks), 6),
        f"macro_recall_at_{golden.top_k}": round(statistics.fmean(recalls), 6),
        f"unique_subject_ratio_at_{golden.top_k}": round(
            statistics.fmean(unique_ratios),
            6,
        ),
    }
    recorded_metrics = result.get("metrics") or {}
    for key, value in metrics.items():
        if recorded_metrics.get(key) != value:
            raise ValueError(f"retrieval metric does not match raw cases: {key}")
    return metrics
