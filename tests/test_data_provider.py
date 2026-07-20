from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from data_profile import DataProfile  # noqa: E402
from providers import SQLitePlanningDataProvider, WeatherProviderError  # noqa: E402
from retrieval import UGCRetrievalHit  # noqa: E402
from tools.types import POI, SearchConstraints  # noqa: E402


def _poi(category: str) -> POI:
    return POI(
        id=f"poi-{category}",
        name=f"{category} candidate",
        category_lv1=category,
        category_lv2=None,
        category_lv3=None,
        typecode=None,
        district=None,
        business_area=None,
        address=None,
        longitude=116.4,
        latitude=39.9,
        rating=4.5,
        avg_price=80,
        open_time=None,
        phone=None,
    )


def _profile() -> DataProfile:
    return DataProfile(
        name="demo",
        classification="synthetic",
        public_reproducible=True,
        sources={"pois": "fixture-pois", "ugc": "fixture-ugc", "routes": "estimated"},
        counts={"pois": 2},
        limitations=("not live",),
    )


def _retrieval_hit() -> UGCRetrievalHit:
    return UGCRetrievalHit(
        record_id="ugc-fixture",
        poi_name="food candidate",
        area_anchor="五道营-雍和宫片区",
        aspect_type="food",
        sentiment="positive",
        confidence=0.9,
        evidence_summary="适合当前查询的可复现证据",
        lexical_score=3.0,
        final_score=4.0,
        matched_features=("bm25", "aspect:food"),
        expanded_terms=("低油",),
    )


def test_provider_merges_independent_results_with_provenance() -> None:
    captured = {}

    def search(*, category, **kwargs):
        del kwargs
        return [_poi(category)]

    def retrieve(query, **kwargs):
        captured["retrieval"] = (query, kwargs)
        return [_retrieval_hit()]

    provider = SQLitePlanningDataProvider(
        search=search,
        summarize=lambda area: {"area": area},
        retrieve=retrieve,
        profile_loader=_profile,
    )
    snapshot = provider.collect(
        query="带娃低油吃饭",
        area_anchor="五道营-雍和宫片区",
        constraints=SearchConstraints(),
        categories=("food", "museum"),
    )

    assert list(snapshot.candidates) == ["food", "museum"]
    assert snapshot.candidates["food"][0].id == "poi-food"
    assert snapshot.area_summary["area"] == "五道营-雍和宫片区"
    assert {item.domain for item in snapshot.evidence} == {"poi", "ugc", "route", "weather"}
    assert all(item.bookable is False for item in snapshot.evidence)
    assert all(item.freshness == "not_applicable" for item in snapshot.evidence)
    assert snapshot.retrieved_evidence[0].item_id == "ugc-fixture"
    assert snapshot.retrieved_evidence[0].algorithm == "bm25_domain_expansion_diversity_v1"
    assert captured["retrieval"][0] == "带娃低油吃饭"
    assert captured["retrieval"][1]["area_anchor"] == "五道营-雍和宫片区"
    assert snapshot.issues == ()
    assert snapshot.weather is not None
    assert snapshot.weather.classification == "synthetic"
    assert snapshot.weather.cache_status == "fixture"


def test_provider_preserves_partial_failure_as_structured_issue() -> None:
    def search(*, category, **kwargs):
        del kwargs
        if category == "museum":
            raise sqlite3.OperationalError("fixture branch failed")
        return [_poi(category)]

    provider = SQLitePlanningDataProvider(
        search=search,
        summarize=lambda area: {},
        retrieve=lambda *args, **kwargs: (),
        profile_loader=_profile,
    )
    snapshot = provider.collect(
        query="下午出去玩",
        area_anchor="五道营-雍和宫片区",
        constraints=SearchConstraints(),
        categories=("food", "museum"),
    )

    assert snapshot.candidates["food"]
    assert snapshot.candidates["museum"] == ()
    assert snapshot.issues[0].domain == "poi:museum"
    assert snapshot.issues[0].retryable is True
    assert snapshot.issues[0].required is False


def test_provider_reports_missing_diet_evidence_as_typed_degradation() -> None:
    provider = SQLitePlanningDataProvider(
        search=lambda *, category, **kwargs: [] if category == "food" else [_poi(category)],
        summarize=lambda area: {"area": area},
        retrieve=lambda *args, **kwargs: (),
        profile_loader=_profile,
    )
    snapshot = provider.collect(
        query="海鲜过敏，下午出去玩",
        area_anchor="五道营-雍和宫片区",
        constraints=SearchConstraints(diet_flags=["no_shellfish"]),
        categories=("food", "museum"),
    )

    assert snapshot.candidates["food"] == ()
    assert snapshot.candidates["museum"]
    issue = next(item for item in snapshot.issues if item.code == "diet_evidence_unavailable")
    assert issue.domain == "poi:food"
    assert issue.required is False
    assert issue.retryable is False
    assert "no_shellfish" not in issue.message


def test_weather_failure_is_optional_typed_degradation() -> None:
    class FailingWeather:
        def forecast(self, request):
            del request
            raise WeatherProviderError(
                code="weather_rate_limited",
                retryable=True,
                message="Weather provider rate limit was reached.",
            )

    provider = SQLitePlanningDataProvider(
        search=lambda *, category, **kwargs: [_poi(category)],
        summarize=lambda area: {"area": area},
        retrieve=lambda *args, **kwargs: (),
        profile_loader=_profile,
        weather_provider=FailingWeather(),
    )

    snapshot = provider.collect(
        query="下午出去玩",
        area_anchor="五道营-雍和宫片区",
        constraints=SearchConstraints(),
        categories=("food",),
    )

    assert snapshot.candidates["food"]
    assert snapshot.weather is None
    issue = next(item for item in snapshot.issues if item.domain == "weather")
    assert issue.code == "weather_rate_limited"
    assert issue.retryable is True
    assert issue.required is False
