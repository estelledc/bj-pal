import unittest


class _FailingVisionClient:
    @property
    def name(self) -> str:
        return "failing-vision"

    def vision_complete(self, *args, **kwargs):
        raise RuntimeError("vision unavailable")


class _FailingTextClient:
    @property
    def name(self) -> str:
        return "failing-text"

    def complete(self, *args, **kwargs):
        raise RuntimeError("text unavailable")


class _EmptyTextClient:
    @property
    def name(self) -> str:
        return "empty-text"

    def complete(self, *args, **kwargs):
        from src.agents.llm_client import LLMResponse

        parsed = {
            "area_anchor": "",
            "poi_name": "",
            "taste_tags": [],
            "scene_tags": [],
            "risk_tags": [],
            "aspects": [],
        }
        return LLMResponse(text="{}", parsed=parsed)


class MultimodalIntakeTest(unittest.TestCase):
    def test_risk_only_text_is_not_empty(self) -> None:
        from src.agents.text_intake import TextIntakeResult

        result = TextIntakeResult(risk_tags=["queue_long"])

        self.assertFalse(result.is_empty())

    def test_ui_text_intake_uses_llm_when_available(self) -> None:
        from src.agents.llm_client import MockLLMClient
        from src.ui.multimodal_intake import extract_text_for_ui

        result = extract_text_for_ui(
            "不要排队太久，想吃清淡一点，最好安静",
            client=MockLLMClient(),
        )

        self.assertEqual(result.source, "llm")
        self.assertIn("queue_long", result.risk_tags)
        self.assertIn("light", result.taste_tags)
        self.assertIn("quiet", result.scene_tags)

    def test_ui_text_intake_falls_back_to_rules_when_llm_fails(self) -> None:
        from src.ui.multimodal_intake import extract_text_for_ui

        result = extract_text_for_ui(
            "不要排队太久，想吃清淡一点，最好安静",
            client=_FailingTextClient(),
        )

        self.assertEqual(result.source, "rules")
        self.assertIn("queue_long", result.risk_tags)
        self.assertIn("light", result.taste_tags)
        self.assertIn("quiet", result.scene_tags)

    def test_ui_text_intake_falls_back_when_llm_returns_empty(self) -> None:
        from src.ui.multimodal_intake import extract_text_for_ui

        result = extract_text_for_ui(
            "不要排队太久，想吃清淡一点，最好安静",
            client=_EmptyTextClient(),
        )

        self.assertEqual(result.source, "rules")
        self.assertIn("queue_long", result.risk_tags)

    def test_sample_selector_can_clear_and_fill_text(self) -> None:
        from src.ui.multimodal_intake import _sample_text_for_index

        self.assertEqual(_sample_text_for_index(0), "")
        self.assertIn("五道营", _sample_text_for_index(1))

    def test_widget_keys_are_namespaced_by_render_location(self) -> None:
        from src.ui.multimodal_intake import _mm_key

        self.assertEqual(_mm_key("mm_inline", "sample_idx"), "mm_inline_sample_idx")
        self.assertEqual(_mm_key("mm_supporting", "sample_idx"), "mm_supporting_sample_idx")
        self.assertNotEqual(
            _mm_key("mm_inline", "sample_idx"),
            _mm_key("mm_supporting", "sample_idx"),
        )

    def test_ui_image_intake_falls_back_to_mock_when_vision_fails(self) -> None:
        from src.ui.multimodal_intake import extract_image_for_ui

        result = extract_image_for_ui(
            b"not-a-real-image",
            image_mime="image/png",
            client=_FailingVisionClient(),
        )

        self.assertEqual(result.source, "vision_mock")
        self.assertTrue(result.area_anchor)
        self.assertTrue(result.poi_name)
        self.assertTrue(result.aspects)


if __name__ == "__main__":
    unittest.main()
