import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


class _GenericMemoryIntakeClient:
    @property
    def name(self) -> str:
        return "generic-memory-intake"

    def complete(self, *args, **kwargs):
        from agents.llm_client import LLMResponse

        parsed = {
            "area_anchor": "",
            "poi_name": "",
            "taste_tags": ["vinegar_flavor"],
            "scene_tags": ["quiet"],
            "risk_tags": ["medical_diet_risk"],
            "diet_flags": ["no_lactose", "low_purine"],
            "preference_tags": ["sour_food"],
            "avoid_tags": ["buffet", "raw_seafood"],
            "aspects": [
                {
                    "aspect_type": "food",
                    "sentiment": "mixed",
                    "confidence": 0.88,
                    "evidence_summary": "用户给出了健康忌口和酸口偏好",
                    "normalized_value": {
                        "diet_flags": ["no_lactose", "low_purine"],
                        "preference_tags": ["sour_food"],
                        "avoid_tags": ["buffet"],
                    },
                }
            ],
        }
        return LLMResponse(text="{}", parsed=parsed)


class _ManualMemoryProfileClient:
    @property
    def name(self) -> str:
        return "manual-memory-profile"

    def complete(self, *args, **kwargs):
        from agents.llm_client import LLMResponse

        parsed = {
            "area_anchor": "",
            "poi_name": "",
            "taste_tags": ["watermelon"],
            "scene_tags": [],
            "risk_tags": ["urticaria"],
            "diet_flags": ["no_lactose"],
            "preference_tags": ["buffet"],
            "avoid_tags": [],
            "aspects": [
                {
                    "aspect_type": "food",
                    "sentiment": "mixed",
                    "confidence": 0.9,
                    "evidence_summary": "用户给出乳糖不耐受、荨麻疹、西瓜和自助餐偏好",
                    "normalized_value": {
                        "diet_flags": ["no_lactose"],
                        "risk_tags": ["urticaria"],
                        "taste_tags": ["watermelon"],
                        "preference_tags": ["buffet"],
                    },
                }
            ],
        }
        return LLMResponse(text="{}", parsed=parsed)


class _EmptyMemoryProfileClient:
    @property
    def name(self) -> str:
        return "empty-memory-profile"

    def complete(self, *args, **kwargs):
        from agents.llm_client import LLMResponse

        return LLMResponse(text="{}", parsed={
            "area_anchor": "",
            "poi_name": "",
            "taste_tags": [],
            "scene_tags": [],
            "risk_tags": [],
            "diet_flags": [],
            "preference_tags": [],
            "avoid_tags": [],
            "aspects": [],
        })


class UserMemoryLLMIntakeTest(unittest.TestCase):
    def test_infer_records_generic_llm_diet_and_preference_tags(self) -> None:
        from agents.user_memory import forget_all, get_preferences, infer_from_user_input

        user_id = f"u-test-{uuid.uuid4().hex[:8]}"
        try:
            infer_from_user_input(
                user_id,
                "我乳糖不耐受，痛风需要低嘌呤，但喜欢酸口和醋味，不想吃自助",
                client=_GenericMemoryIntakeClient(),
                use_llm=True,
            )
            by_key = {entry.mem_key: entry for entry in get_preferences(user_id)}

            self.assertEqual(by_key["diet:no_lactose"].kind, "dislike")
            self.assertEqual(by_key["diet:low_purine"].kind, "preference")
            self.assertEqual(by_key["preference:sour_food"].kind, "preference")
            self.assertEqual(by_key["taste:vinegar_flavor"].kind, "preference")
            self.assertEqual(by_key["avoid:buffet"].kind, "dislike")
            self.assertEqual(by_key["avoid:raw_seafood"].kind, "dislike")
        finally:
            forget_all(user_id)

    def test_manual_memory_uses_llm_result_without_rule_hallucinations(self) -> None:
        from agents.user_memory import forget_all, get_preferences, infer_from_user_input

        user_id = f"u-test-{uuid.uuid4().hex[:8]}"
        try:
            infer_from_user_input(
                user_id,
                "乳糖不耐受，寻麻疹，喜欢吃西瓜，自助餐",
                client=_ManualMemoryProfileClient(),
                use_llm=True,
            )
            by_key = {entry.mem_key: entry for entry in get_preferences(user_id)}

            self.assertEqual(by_key["diet:no_lactose"].kind, "dislike")
            self.assertEqual(by_key["risk:urticaria"].kind, "dislike")
            self.assertEqual(by_key["taste:watermelon"].kind, "preference")
            self.assertEqual(by_key["preference:buffet"].kind, "preference")
            self.assertNotIn("diet:no_seafood", by_key)
            self.assertNotIn("risk:allergy", by_key)
        finally:
            forget_all(user_id)

    def test_llm_memory_does_not_apply_keyword_rules_when_llm_extracts_nothing(self) -> None:
        from agents.user_memory import forget_all, get_preferences, infer_from_user_input

        user_id = f"u-test-{uuid.uuid4().hex[:8]}"
        try:
            infer_from_user_input(
                user_id,
                "想清淡一点，不要走太远",
                client=_EmptyMemoryProfileClient(),
                use_llm=True,
            )

            self.assertEqual(get_preferences(user_id), [])
        finally:
            forget_all(user_id)

    def test_memory_intake_does_not_write_when_llm_is_disabled(self) -> None:
        from agents.user_memory import forget_all, get_preferences, infer_from_user_input

        user_id = f"u-test-{uuid.uuid4().hex[:8]}"
        try:
            written = infer_from_user_input(
                user_id,
                "清淡，带娃，不吃辣，咖啡，乳糖不耐受",
                use_llm=False,
            )

            self.assertEqual(written, [])
            self.assertEqual(get_preferences(user_id), [])
        finally:
            forget_all(user_id)

    def test_planner_reads_memory_without_auto_writing_new_memory(self) -> None:
        from agents.llm_client import MockLLMClient
        from agents.planner import plan
        from agents.user_memory import forget_all, get_preferences

        user_id = f"u-test-{uuid.uuid4().hex[:8]}"
        try:
            with patch(
                "agents.user_memory.infer_from_user_input",
                side_effect=AssertionError("planner must not write memory"),
            ):
                p = plan(
                    user_input="清淡，带娃，不吃辣，咖啡",
                    persona="family",
                    user_id=user_id,
                    client=MockLLMClient(),
                )

            self.assertGreaterEqual(len(p.steps), 1)
            self.assertEqual(get_preferences(user_id), [])
        finally:
            forget_all(user_id)

    def test_text_intake_prompt_guides_llm_on_manual_memory_examples(self) -> None:
        from agents.text_intake import TEXT_INTAKE_SYSTEM

        self.assertIn("乳糖不耐受", TEXT_INTAKE_SYSTEM)
        self.assertIn("寻麻疹", TEXT_INTAKE_SYSTEM)
        self.assertIn("西瓜", TEXT_INTAKE_SYSTEM)
        self.assertIn("自助餐", TEXT_INTAKE_SYSTEM)


if __name__ == "__main__":
    unittest.main()
