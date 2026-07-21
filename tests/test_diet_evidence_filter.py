from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.amap_search import search_pois  # noqa: E402
from agents.llm_client import MockLLMClient  # noqa: E402
from agents.planner import plan  # noqa: E402
from agents.types import UserPreferences  # noqa: E402
from tools.types import SearchConstraints  # noqa: E402
from tools.ugc_signals import fetch_aspects  # noqa: E402


def _positive_taste_tags(poi_name: str) -> set[str]:
    tags: set[str] = set()
    for aspect in fetch_aspects(poi_name=poi_name, aspect_types=["food"]):
        if aspect.sentiment != "positive" or aspect.confidence < 0.6 or aspect.needs_review:
            continue
        tags.update(aspect.normalized_value.get("taste_tags") or [])
    return tags


def test_no_spicy_food_candidates_require_positive_structured_evidence() -> None:
    candidates = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="food",
        constraints=SearchConstraints(
            persona="family",
            has_child=True,
            child_age=5,
            diet_flags=["no_spicy"],
            budget_per_person=150,
            min_rating=4.0,
            walk_radius_km=0.8,
        ),
        limit=20,
    )

    assert candidates
    assert all("no_spicy" in _positive_taste_tags(poi.name) for poi in candidates)
    assert "雍和轻食小馆" in {poi.name for poi in candidates}


def test_supported_diet_filter_does_not_remove_non_food_candidates() -> None:
    candidates = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="all",
        constraints=SearchConstraints(
            diet_flags=["no_spicy"],
            min_rating=4.0,
            walk_radius_km=0.8,
        ),
        limit=100,
    )

    assert any(poi.category_lv1 != "餐饮服务" for poi in candidates)
    assert all(
        poi.category_lv1 != "餐饮服务"
        or "no_spicy" in _positive_taste_tags(poi.name)
        for poi in candidates
    )


def test_unknown_diet_flag_fails_closed_instead_of_returning_ordinary_food() -> None:
    candidates = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="food",
        constraints=SearchConstraints(
            diet_flags=["no_shellfish"],
            budget_per_person=150,
            min_rating=4.0,
            walk_radius_km=0.8,
        ),
        limit=20,
    )

    assert candidates == []


def test_unknown_diet_flag_omits_food_steps_and_surfaces_typed_warning() -> None:
    result = plan(
        user_input="海鲜过敏，下午在五道营附近逛逛",
        prefs=UserPreferences(
            persona="solo",
            party_size=1,
            diet_flags=["no_shellfish"],
            target_start="14:00",
            duration_hours=3,
            raw_input="海鲜过敏，下午在五道营附近逛逛",
        ),
        area_anchor="五道营-雍和宫片区",
        client=MockLLMClient(),
    )

    assert all(step.kind not in {"meal", "snack"} for step in result.steps)
    warning = next(
        item for item in result.data_warnings
        if item["code"] == "diet_evidence_unavailable"
    )
    assert warning["domain"] == "poi:food"
    assert warning["required"] is False
