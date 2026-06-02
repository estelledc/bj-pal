import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.replanner import replan_step  # noqa: E402
from agents.types import Plan, Step, UserPreferences  # noqa: E402
from tools.availability_probe import ProbeResult  # noqa: E402
from tools.amap_search import search_pois  # noqa: E402
from tools.types import SearchConstraints  # noqa: E402


class RerouteMemoryTest(unittest.TestCase):
    def test_replan_excludes_session_history_for_repeated_user_dissent(self) -> None:
        plan = Plan(
            persona="friends",
            area_anchor="五道营-雍和宫片区",
            steps=[
                Step(
                    step_index=1,
                    kind="meal",
                    poi_id="P_HTKF",
                    poi_name="胡同咖啡(五道营店)",
                    start_time="14:00",
                    duration_min=60,
                ),
            ],
        )
        probe_result = ProbeResult(
            poi_id="P_HTKF",
            poi_name="胡同咖啡(五道营店)",
            status="user_dissent",
            reason="user_dissent",
            fallback_action="reroute",
            evidence=["用户已经点过一次换一个，不想重复看旧地点"],
        )
        previously_seen = {
            "方砖厂69号炸酱面(雍和宫店)",
            "胡同咖啡(五道营店)",
        }

        new_plan, event = replan_step(
            plan,
            failed_step_idx=0,
            probe_result=probe_result,
            prefs=UserPreferences(persona="friends", budget_per_person=180),
            excluded_poi_names=previously_seen,
        )

        self.assertIsNotNone(event.replacement_poi_name)
        self.assertNotIn(event.replacement_poi_name, previously_seen)
        self.assertNotIn(new_plan.steps[0].poi_name, previously_seen)

    def test_no_alternative_user_dissent_uses_user_facing_copy(self) -> None:
        plan = Plan(
            persona="friends",
            area_anchor="五道营-雍和宫片区",
            steps=[
                Step(
                    step_index=1,
                    kind="meal",
                    poi_id="P_HTKF",
                    poi_name="胡同咖啡(五道营店)",
                    start_time="14:00",
                    duration_min=60,
                ),
            ],
        )
        probe_result = ProbeResult(
            poi_id="P_HTKF",
            poi_name="胡同咖啡(五道营店)",
            status="user_dissent",
            reason="user_dissent",
            wait_min=0,
            fallback_action="reroute",
            evidence=["用户点击换一个"],
        )
        constraints = SearchConstraints(
            persona="friends",
            budget_per_person=180,
            min_rating=4.0,
            walk_radius_km=1.5,
        )
        all_known_food = {
            poi.name
            for poi in search_pois(
                area_anchor="五道营-雍和宫片区",
                category="food",
                constraints=constraints,
                limit=50,
            )
        }

        new_plan, event = replan_step(
            plan,
            failed_step_idx=0,
            probe_result=probe_result,
            prefs=UserPreferences(persona="friends", budget_per_person=180),
            excluded_poi_names=all_known_food,
        )

        self.assertIsNone(event.replacement_poi_name)
        self.assertNotIn("user_dissent", new_plan.steps[0].rationale)
        self.assertNotIn("wait=0", new_plan.steps[0].rationale)
        self.assertIn("已经看过", new_plan.steps[0].rationale)


if __name__ == "__main__":
    unittest.main()
