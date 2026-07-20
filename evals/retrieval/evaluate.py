"""Metric calculation and artifact composition for retrieval experiments."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True)
class RetrievalCase:
    case_id: str
    query: str
    area_anchor: str
    relevant_poi_names: tuple[str, ...]


@dataclass(frozen=True)
class GoldenSet:
    name: str
    profile: str
    classification: str
    label_basis: str
    top_k: int
    cases: tuple[RetrievalCase, ...]
    sha256: str


RetrieveSubjects = Callable[[RetrievalCase, int], Sequence[str]]


def load_golden_set(path: Path) -> GoldenSet:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported retrieval golden-set schema")
    cases = tuple(
        RetrievalCase(
            case_id=item["case_id"],
            query=item["query"],
            area_anchor=item["area_anchor"],
            relevant_poi_names=tuple(item["relevant_poi_names"]),
        )
        for item in payload["cases"]
    )
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("retrieval cases must be non-empty and uniquely identified")
    if any(not case.relevant_poi_names for case in cases):
        raise ValueError("every retrieval case needs at least one relevant POI")
    top_k = int(payload["top_k"])
    if top_k < 1:
        raise ValueError("top_k must be positive")
    return GoldenSet(
        name=payload["name"],
        profile=payload["profile"],
        classification=payload["classification"],
        label_basis=payload["label_basis"],
        top_k=top_k,
        cases=cases,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def evaluate_retrievers(
    golden: GoldenSet,
    retrievers: dict[str, RetrieveSubjects],
) -> dict:
    if not retrievers:
        raise ValueError("at least one retriever is required")
    return {
        name: _evaluate_one(golden, retrieve)
        for name, retrieve in retrievers.items()
    }


def _evaluate_one(golden: GoldenSet, retrieve: RetrieveSubjects) -> dict:
    raw_cases: list[dict] = []
    latencies_ms: list[float] = []
    reciprocal_ranks: list[float] = []
    recalls: list[float] = []
    hits = 0
    unique_ratios: list[float] = []

    for case in golden.cases:
        started = time.perf_counter()
        subjects = list(retrieve(case, golden.top_k))[:golden.top_k]
        latency_ms = (time.perf_counter() - started) * 1000
        latencies_ms.append(latency_ms)

        relevant = set(case.relevant_poi_names)
        rank = next(
            (index for index, subject in enumerate(subjects, start=1) if subject in relevant),
            None,
        )
        if rank is not None:
            hits += 1
        reciprocal_rank = 1.0 / rank if rank is not None else 0.0
        reciprocal_ranks.append(reciprocal_rank)
        found_relevant = sorted(relevant.intersection(subjects))
        recall = len(found_relevant) / len(relevant)
        recalls.append(recall)
        unique_ratios.append(len(set(subjects)) / len(subjects) if subjects else 1.0)
        raw_cases.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "area_anchor": case.area_anchor,
                "relevant_poi_names": list(case.relevant_poi_names),
                "returned_poi_names": subjects,
                "found_relevant_poi_names": found_relevant,
                f"recall_at_{golden.top_k}": round(recall, 6),
                "first_relevant_rank": rank,
                "latency_ms": round(latency_ms, 3),
            }
        )

    count = len(golden.cases)
    return {
        "metrics": {
            f"hit_rate_at_{golden.top_k}": round(hits / count, 6),
            f"mrr_at_{golden.top_k}": round(statistics.fmean(reciprocal_ranks), 6),
            f"macro_recall_at_{golden.top_k}": round(statistics.fmean(recalls), 6),
            f"unique_subject_ratio_at_{golden.top_k}": round(
                statistics.fmean(unique_ratios), 6
            ),
            "latency_ms_p50": round(statistics.median(latencies_ms), 3),
            "latency_ms_p95": round(_percentile(latencies_ms, 0.95), 3),
        },
        "case_count": count,
        "raw_cases": raw_cases,
    }


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * quantile))
    return ordered[rank - 1]
