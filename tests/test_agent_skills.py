import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.skills import describe_skills, get_skill, run_skill  # noqa: E402


class AgentSkillsTest(unittest.TestCase):
    def test_catalog_contains_reusable_agent_units(self) -> None:
        catalog = describe_skills()
        names = {entry["name"] for entry in catalog}

        self.assertIn("preference_intake", names)
        self.assertIn("poi_search", names)
        self.assertIn("risk_probe", names)
        self.assertIn("candidate_screening", names)

        for entry in catalog:
            self.assertTrue(entry["label"])
            self.assertTrue(entry["description"])
            self.assertIsInstance(entry["input_keys"], list)
            self.assertIsInstance(entry["output_keys"], list)

    def test_preference_intake_skill_returns_structured_preferences(self) -> None:
        result = run_skill(
            "preference_intake",
            {
                "text": "周末去五道营，想喝咖啡，不要排队太久，清淡一点",
                "use_llm": False,
            },
        )

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.skill_name, "preference_intake")
        self.assertEqual(result.output["area_anchor"], "五道营-雍和宫片区")
        self.assertIn("coffee", result.output["taste_tags"])
        self.assertIn("light", result.output["taste_tags"])
        self.assertIn("queue_long", result.output["risk_tags"])
        self.assertIn("rules", result.evidence[0])

    def test_poi_search_skill_returns_real_candidates(self) -> None:
        result = run_skill(
            "poi_search",
            {
                "area_anchor": "五道营-雍和宫片区",
                "category": "food",
                "limit": 3,
                "constraints": {
                    "budget_per_person": 220,
                    "min_rating": 4.0,
                    "walk_radius_km": 1.5,
                },
            },
        )

        self.assertTrue(result.ok, result.error)
        pois = result.output["pois"]
        self.assertGreaterEqual(len(pois), 1)
        self.assertLessEqual(len(pois), 3)
        self.assertIn("center", result.output)
        for poi in pois:
            self.assertTrue(poi["name"])
            self.assertIsNotNone(poi["longitude"])
            self.assertIsNotNone(poi["latitude"])

    def test_risk_probe_skill_is_deterministic_when_seeded(self) -> None:
        payload = {
            "poi": {
                "id": "skill-cafe",
                "name": "技能测试咖啡",
                "category_lv1": "餐饮服务",
                "category_lv2": "咖啡厅",
                "rating": 4.8,
                "longitude": 116.4166,
                "latitude": 39.9474,
            },
            "party_size": 3,
            "target_time": "2026-06-06T14:00",
            "seed": 7,
            "enable_weather": False,
            "enable_closed": False,
        }

        first = run_skill("risk_probe", payload)
        second = run_skill("risk_probe", payload)

        self.assertTrue(first.ok, first.error)
        self.assertTrue(second.ok, second.error)
        self.assertEqual(first.output["status"], second.output["status"])
        self.assertEqual(first.output["wait_min"], second.output["wait_min"])
        self.assertIsInstance(first.output["evidence"], list)
        self.assertIn("fallback_action", first.output)

    def test_candidate_screening_skill_returns_ranked_shortlist(self) -> None:
        result = run_skill(
            "candidate_screening",
            {
                "user_input": "6 人生日饭，预算 300，别太吵",
                "persona": "family",
                "area_anchor": "王府井-东单片区",
                "category": "food",
                "top_k": 3,
                "prefs": {
                    "party_size": 6,
                    "budget_per_person": 300,
                    "target_start": "18:00",
                },
            },
        )

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.output["mode"], "screening")
        self.assertLessEqual(len(result.output["candidates"]), 3)
        self.assertNotIn("steps", result.output)
        if result.output["candidates"]:
            top = result.output["candidates"][0]
            self.assertIn("poi_name", top)
            self.assertIn("score", top)

    def test_unknown_skill_name_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            get_skill("missing_skill")


if __name__ == "__main__":
    unittest.main()
