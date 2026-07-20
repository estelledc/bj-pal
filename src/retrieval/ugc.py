"""Explainable UGC retrieval built on the project's lexical BM25 index.

The legacy BM25 helper is intentionally kept as the lexical baseline. This
adapter adds three production-facing behaviors without hiding them in an LLM:

* deterministic domain query expansion;
* field-aware, inspectable score adjustments;
* POI-level diversity so duplicate evidence cannot consume every top-k slot.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Sequence

from tools.ugc_bm25 import BM25Hit, search as bm25_search


SearchFunction = Callable[..., list[BM25Hit]]


@dataclass(frozen=True)
class UGCRetrievalHit:
    record_id: str
    poi_name: str
    area_anchor: str
    aspect_type: str
    sentiment: str
    confidence: float
    evidence_summary: str
    lexical_score: float
    final_score: float
    matched_features: tuple[str, ...]
    expanded_terms: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


# These are domain vocabulary aliases, not generated facts. Expansion is kept
# deliberately small and exposed on every hit for replay and interview review.
_EXPANSION_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("夏天", "避晒"), ("夏季", "暴晒", "烈日", "树荫")),
    (("五岁", "5岁", "5 岁"), ("5岁", "5 岁")),
    (("排队", "等位"), ("排队", "等位", "拥挤")),
    (("停车", "自驾"), ("停车", "自驾", "停车位")),
    (("雨天", "看展"), ("室内", "展览")),
    (("低糖", "减脂"), ("低糖", "减脂", "低油")),
)

_ASPECT_INTENTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("排队", "等位", "拥挤"), ("queue", "crowd")),
    (("停车", "自驾", "地铁", "步行"), ("transport", "comfort")),
    (("安静", "插座", "卫生间", "环境"), ("environment", "comfort")),
    (("吃饭", "加餐", "低糖", "低油", "减脂"), ("food",)),
    (("路线", "看展", "文化", "雨天", "室内"), ("scenario_fit", "environment")),
)


def expand_query(query: str) -> tuple[str, tuple[str, ...]]:
    """Return a deterministic expanded query and the terms that were added."""
    normalized = " ".join(str(query or "").split())
    if not normalized:
        return "", ()

    additions: list[str] = []
    for triggers, candidates in _EXPANSION_RULES:
        if any(trigger in normalized for trigger in triggers):
            for term in candidates:
                if term not in normalized and term not in additions:
                    additions.append(term)
    return " ".join((normalized, *additions)), tuple(additions)


class ExplainableUGCRetriever:
    """Retrieve diverse UGC evidence while preserving score provenance."""

    algorithm = "bm25_domain_expansion_diversity_v1"

    def __init__(self, *, search: SearchFunction = bm25_search) -> None:
        self._search = search

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        area_anchor: str | None = None,
    ) -> tuple[UGCRetrievalHit, ...]:
        if top_k < 1 or top_k > 100:
            raise ValueError("top_k must be between 1 and 100")

        expanded_query, expanded_terms = expand_query(query)
        if not expanded_query:
            return ()

        # Over-fetch before POI diversity collapse. This also makes the legacy
        # duplicate-heavy corpus observable rather than silently truncating it.
        raw_hits = self._search(
            expanded_query,
            top_k=max(100, top_k * 20),
            area_anchor=area_anchor,
            boost_weekend_afternoon=False,
        )
        scored = [
            self._score(hit, query=query, expanded_terms=expanded_terms)
            for hit in raw_hits
        ]
        scored.sort(key=lambda hit: (-hit.final_score, hit.record_id))

        diverse: list[UGCRetrievalHit] = []
        seen_subjects: set[str] = set()
        for hit in scored:
            subject = hit.poi_name.strip() or hit.record_id
            if subject in seen_subjects:
                continue
            seen_subjects.add(subject)
            diverse.append(hit)
            if len(diverse) == top_k:
                break
        return tuple(diverse)

    @staticmethod
    def _score(
        hit: BM25Hit,
        *,
        query: str,
        expanded_terms: Sequence[str],
    ) -> UGCRetrievalHit:
        score = float(hit.score)
        features: list[str] = ["bm25"]

        normalized_query = " ".join(str(query or "").split())
        poi_name = hit.poi_name.strip()
        if poi_name and (poi_name in normalized_query or normalized_query in poi_name):
            score += 3.0
            features.append("poi_name_exact")

        for triggers, aspects in _ASPECT_INTENTS:
            if any(trigger in normalized_query for trigger in triggers) and hit.aspect_type in aspects:
                score += 1.0
                features.append(f"aspect:{hit.aspect_type}")
                break

        confidence = min(1.0, max(0.0, float(hit.confidence)))
        score += 0.25 * confidence
        features.append("confidence_tiebreak")

        return UGCRetrievalHit(
            record_id=hit.record_id,
            poi_name=hit.poi_name,
            area_anchor=hit.area_anchor,
            aspect_type=hit.aspect_type,
            sentiment=hit.sentiment,
            confidence=confidence,
            evidence_summary=hit.evidence_summary,
            lexical_score=round(float(hit.score), 6),
            final_score=round(score, 6),
            matched_features=tuple(features),
            expanded_terms=tuple(expanded_terms),
        )
