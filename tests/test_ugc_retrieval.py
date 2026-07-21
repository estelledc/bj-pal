from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from retrieval import ExplainableUGCRetriever, expand_query  # noqa: E402
from tools.ugc_bm25 import BM25Hit  # noqa: E402


def _hit(
    record_id: str,
    poi_name: str,
    score: float,
    *,
    aspect_type: str = "queue",
) -> BM25Hit:
    return BM25Hit(
        record_id=record_id,
        poi_name=poi_name,
        area_anchor="五道营-雍和宫片区",
        aspect_type=aspect_type,
        sentiment="negative",
        confidence=0.8,
        evidence_summary=f"{poi_name} 的测试证据",
        score=score,
    )


def test_query_expansion_is_deterministic_and_visible() -> None:
    expanded, terms = expand_query("夏天下午带娃避晒")
    assert expanded.startswith("夏天下午带娃避晒")
    assert terms == ("夏季", "暴晒", "烈日", "树荫")
    assert expand_query(expanded)[1] == ()


def test_retriever_collapses_duplicate_pois_and_preserves_score_provenance() -> None:
    captured = {}

    def search(query, **kwargs):
        captured["query"] = query
        captured["kwargs"] = kwargs
        return [
            _hit("ugc-1", "同一地点", 10.0),
            _hit("ugc-2", "同一地点", 9.0),
            _hit("ugc-3", "另一个地点", 8.0),
        ]

    hits = ExplainableUGCRetriever(search=search).retrieve(
        "周末排队",
        top_k=2,
        area_anchor="五道营-雍和宫片区",
    )

    assert [hit.poi_name for hit in hits] == ["同一地点", "另一个地点"]
    assert hits[0].lexical_score == 10.0
    assert hits[0].final_score > hits[0].lexical_score
    assert "bm25" in hits[0].matched_features
    assert "aspect:queue" in hits[0].matched_features
    assert "拥挤" in hits[0].expanded_terms
    assert captured["kwargs"]["top_k"] == 100
    assert captured["kwargs"]["boost_weekend_afternoon"] is False


def test_retriever_rejects_invalid_limits_and_empty_queries() -> None:
    retriever = ExplainableUGCRetriever(search=lambda *args, **kwargs: [])
    assert retriever.retrieve("   ") == ()
    with pytest.raises(ValueError, match="top_k"):
        retriever.retrieve("query", top_k=0)
