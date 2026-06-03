from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.select_showcase_cases import build_showcase_report, render_markdown  # noqa: E402


def _case(
    scenario: str,
    title: str,
    persona: str,
    *,
    step_names: list[str],
    events: list[dict] | None = None,
    diet_flags: list[str] | None = None,
    area_anchor: str = "五道营-雍和宫片区",
    user_input: str = "周末下午想出去玩，路线要清楚。",
) -> dict:
    steps = [
        {
            "step_index": i + 1,
            "kind": "depart" if name == "返程" else "citywalk",
            "start_time": f"{14 + i:02d}:00",
            "duration_min": 45 if name != "返程" else 0,
            "poi_id": None if name == "返程" else f"poi-{scenario}-{i}",
            "poi_name": name,
            "mode_to_here": "walking",
            "rationale": f"{name} 符合用户输入里的偏好，适合文档展示。",
            "is_rerouted": False,
        }
        for i, name in enumerate(step_names)
    ]
    return {
        "scenario": scenario,
        "title": title,
        "ok": True,
        "input": {
            "user_input": user_input,
            "persona": persona,
            "area_anchor": area_anchor,
            "prefs": {
                "persona": persona,
                "party_size": 3,
                "diet_flags": diet_flags or [],
                "target_start": "14:00",
                "duration_hours": 4.0,
            },
        },
        "events": events or [],
        "v2": {
            "persona": persona,
            "area_anchor": "五道营-雍和宫片区",
            "summary": f"{title} 的可展示路线总结",
            "steps": steps,
        },
        "timing": {"total_s": 9.5},
    }


class ShowcaseSelectorTest(unittest.TestCase):
    def test_build_showcase_report_selects_diverse_high_quality_cases(self) -> None:
        family = _case(
            "S01",
            "亲子忌口记忆",
            "family",
            step_names=["雍和宫", "国子监", "素食餐厅", "安静咖啡", "返程"],
            events=[{
                "failed_poi_name": "雍和宫",
                "replacement_poi_name": "国子监",
                "reason": "queue_60min",
            }],
            diet_flags=["no_lactose", "urticaria"],
            user_input="乳糖不耐受，寻麻疹，喜欢吃西瓜，自助餐；带娃周末下午出去。",
        )
        friends = _case(
            "S11",
            "朋友雨天室内",
            "friends",
            step_names=["太古里", "设计书店", "粤菜餐厅", "精品咖啡", "返程"],
        )
        weak = _case(
            "S99",
            "重复地点弱案例",
            "solo",
            step_names=["同一个地点", "同一个地点", "返程"],
        )

        report = build_showcase_report(
            [weak, family, friends],
            selected_count=2,
            backend="mock",
            source_path="unit-test.json",
        )

        self.assertEqual(report["total_candidates_run"], 3)
        self.assertEqual(report["selected_count"], 2)
        selected_ids = [case["scenario"] for case in report["selected_cases"]]
        self.assertIn("S01", selected_ids)
        self.assertIn("S11", selected_ids)
        self.assertNotIn("S99", selected_ids)
        self.assertEqual(len({case["persona"] for case in report["selected_cases"]}), 2)

        first = report["selected_cases"][0]
        self.assertIn("why_selected", first)
        self.assertGreater(first["metrics"]["showcase_score"], 0)
        self.assertGreaterEqual(len(first["observed_result"]["steps"]), 4)
        self.assertIn("all_case_summaries", report)

    def test_render_markdown_is_doc_ready(self) -> None:
        report = build_showcase_report(
            [
                _case(
                    "S31",
                    "陪父母慢节奏",
                    "with_parents",
                    step_names=["景山公园", "老字号餐厅", "什刹海", "茶馆", "返程"],
                )
            ],
            selected_count=1,
            backend="mock",
            source_path="unit-test.json",
        )

        markdown = render_markdown(report)

        self.assertIn("# Showcase 测试案例", markdown)
        self.assertIn("总候选数：1", markdown)
        self.assertIn("陪父母慢节奏", markdown)
        self.assertIn("为什么适合展示", markdown)

    def test_selection_keeps_one_case_per_persona_when_available(self) -> None:
        results = [
            _case("S01", "亲子一", "family",
                  step_names=["A1", "A2", "A3", "A4", "返程"],
                  area_anchor="五道营-雍和宫片区",
                  events=[{"failed_poi_name": "A1", "replacement_poi_name": "A2", "reason": "queue"}]),
            _case("S02", "亲子二", "family",
                  step_names=["B1", "B2", "B3", "B4", "返程"],
                  area_anchor="三里屯片区V3",
                  events=[{"failed_poi_name": "B1", "replacement_poi_name": "B2", "reason": "queue"}]),
            _case("S11", "朋友", "friends",
                  step_names=["C1", "C2", "C3", "C4", "返程"]),
            _case("S21", "独自", "solo",
                  step_names=["D1", "D2", "D3", "D4", "返程"]),
            _case("S31", "父母", "with_parents",
                  step_names=["E1", "E2", "E3", "E4", "返程"]),
        ]

        report = build_showcase_report(
            results,
            selected_count=4,
            backend="mock",
            source_path="unit-test.json",
        )

        personas = {case["persona"] for case in report["selected_cases"]}
        self.assertEqual(personas, {"family", "friends", "solo", "with_parents"})

    def test_excluded_scenario_is_not_selected_but_remains_auditable(self) -> None:
        results = [
            _case("S35", "陪父母上香", "with_parents",
                  step_names=["雍和宫", "胡大饭馆", "返程"],
                  events=[{"failed_poi_name": "素斋", "replacement_poi_name": "胡大饭馆", "reason": "queue"}]),
            _case("S32", "陪父母吃馆", "with_parents",
                  step_names=["南门涮肉", "东单公园", "王府井教堂", "返程"]),
            _case("S14", "朋友夜生活", "friends",
                  step_names=["韩餐", "太古里", "夜景", "返程"]),
            _case("S23", "独自逛展", "solo",
                  step_names=["798", "咖啡", "云南菜", "返程"]),
            _case("S01", "亲子周末", "family",
                  step_names=["国子监", "孔庙", "地坛", "返程"]),
        ]

        report = build_showcase_report(
            results,
            selected_count=4,
            backend="mock",
            source_path="unit-test.json",
            excluded_scenarios=["S35"],
        )

        selected_ids = {case["scenario"] for case in report["selected_cases"]}
        self.assertNotIn("S35", selected_ids)
        self.assertEqual(report["excluded_scenarios"], ["S35"])
        s35_summary = next(s for s in report["all_case_summaries"] if s["scenario"] == "S35")
        self.assertTrue(s35_summary["excluded_from_selection"])
        self.assertFalse(s35_summary["selected"])


if __name__ == "__main__":
    unittest.main()
