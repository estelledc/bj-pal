import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.amap_search import search_pois  # noqa: E402
from tools.types import SearchConstraints  # noqa: E402


CORE_AREAS = (
    "五道营-雍和宫片区",
    "三里屯片区",
    "王府井-东单片区",
    "什刹海-鼓楼片区",
    "天安门-故宫片区",
    "奥林匹克公园片区",
    "景山-什刹海片区",
    "东四-本地餐饮片区",
)


class RerouteCandidateCoverageTest(unittest.TestCase):
    def test_core_areas_have_enough_replacement_candidates(self) -> None:
        constraints = SearchConstraints(
            persona="friends",
            budget_per_person=220,
            min_rating=4.0,
            walk_radius_km=1.5,
        )

        failures = []
        for area in CORE_AREAS:
            counts = {
                category: len(search_pois(
                    area_anchor=area,
                    category=category,
                    constraints=constraints,
                    limit=50,
                ))
                for category in ("food", "scenic", "shopping")
            }
            if counts["food"] < 8 or counts["scenic"] < 6 or counts["shopping"] < 3:
                failures.append((area, counts))

        self.assertEqual(failures, [])

    def test_search_api_categories_used_by_planner_have_candidates(self) -> None:
        constraints = SearchConstraints(
            persona="friends",
            budget_per_person=220,
            min_rating=4.0,
            walk_radius_km=1.5,
        )

        failures = []
        for area in CORE_AREAS:
            counts = {
                category: len(search_pois(
                    area_anchor=area,
                    category=category,
                    constraints=constraints,
                    limit=20,
                ))
                for category in ("museum", "sports")
            }
            if counts["museum"] < 2 or counts["sports"] < 2:
                failures.append((area, counts))

        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
