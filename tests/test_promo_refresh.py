from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROMO = ROOT / "promo"

PRIMARY_HTML = [
    PROMO / "pitch-deck.html",
    PROMO / "landing-page.html",
    PROMO / "one-pager.html",
    PROMO / "readme-hero.html",
    PROMO / "xhs-carousel.html",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class PromoRefreshTest(unittest.TestCase):
    def test_primary_promo_assets_credit_jason_and_keepl(self) -> None:
        missing = [path.name for path in PRIMARY_HTML if "Jason Xun · KeepL" not in _read(path)]
        stale = [path.name for path in PRIMARY_HTML if "Jason · KeepL" in _read(path)]

        self.assertEqual(missing, [])
        self.assertEqual(stale, [])

    def test_primary_promo_copy_uses_neutral_partner_language(self) -> None:
        checked = PRIMARY_HTML + [PROMO / "architecture.md"]
        stale = [path.name for path in checked if "老婆" in _read(path)]

        self.assertEqual(stale, [])

    def test_promo_no_longer_uses_stale_ugc_counts(self) -> None:
        checked = PRIMARY_HTML + [PROMO / "architecture.md"]
        stale = []
        for path in checked:
            text = _read(path)
            if "1,102" in text or "1102" in text:
                stale.append(path.name)

        self.assertEqual(stale, [])

    def test_promo_surfaces_latest_v3_1_proof_points(self) -> None:
        combined = "\n".join(_read(path) for path in PRIMARY_HTML + [PROMO / "architecture.md"])

        for expected in ["8,666", "5,198", "280/280", "0.1089", "ToT", "OPTW", "Kemeny+Borda"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, combined)

    def test_promo_positions_data_as_real_world_grounding(self) -> None:
        combined = "\n".join(_read(path) for path in PRIMARY_HTML)

        for expected in [
            "真实场景 Grounding",
            "不是 demo 假数据",
            "POI facts",
            "UGC soft signals",
            "需求先验",
            "reasons[]",
            "evidence[]",
        ]:
            with self.subTest(expected=expected):
                self.assertIn(expected, combined)

        self.assertIn("不是路线生成器，是被真实世界校准过的决策代理", _read(PROMO / "pitch-deck.html"))
        self.assertIn("真实北京，不是 mock itinerary", _read(PROMO / "one-pager.html"))

    def test_promo_html_uses_technology_visual_tokens(self) -> None:
        combined = "\n".join(_read(path) for path in PRIMARY_HTML)

        for expected in ["--cyber", "--neon", "tech-grid", "trace"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, combined)

    def test_pitch_deck_scales_fixed_slide_canvas_to_browser_viewport(self) -> None:
        html = _read(PROMO / "pitch-deck.html")

        self.assertIn('content="width=device-width, initial-scale=1"', html)
        self.assertIn("deck-stage", html)
        self.assertIn("slide-shell", html)
        self.assertIn("slide-frame", html)
        self.assertIn("--deck-scale", html)
        self.assertIn("fitDeck", html)
        self.assertIn("height: 100vh", html)
        self.assertNotIn("width=1920", html)

    def test_one_pager_scales_print_canvas_to_browser_viewport(self) -> None:
        html = _read(PROMO / "one-pager.html")

        self.assertIn('content="width=device-width, initial-scale=1"', html)
        self.assertIn("page-shell", html)
        self.assertIn("--page-scale", html)
        self.assertIn("fitOnePager", html)
        self.assertIn("transform: scale(var(--page-scale))", html)
        self.assertIn("zoom: 0.64008", html)

    def test_pitch_deck_pdf_uses_native_sixteen_by_nine_pages(self) -> None:
        html = _read(PROMO / "pitch-deck.html")

        self.assertIn("@page { size: 1920px 1080px; margin: 0; }", html)
        self.assertIn("width: 1920px", html)
        self.assertIn("height: 1080px", html)

    def test_one_pager_color_system_stays_dark_and_cyber(self) -> None:
        html = _read(PROMO / "one-pager.html")

        self.assertNotIn("rgba(255,255,255,0.4)", html)
        self.assertNotIn("background: var(--ink)", html)
        self.assertNotIn("color: var(--paper)", html)
        self.assertNotIn("rgba(156,42,37", html)
        self.assertIn("rgba(8,25,44", html)
        self.assertIn("rgba(35,217,255", html)

    def test_one_pager_eval_metrics_use_non_overlapping_layout(self) -> None:
        html = _read(PROMO / "one-pager.html")

        self.assertIn("architecture-flow--compact", html)
        self.assertIn("eval-report-grid", html)
        self.assertIn("metric-card", html)
        self.assertNotIn("eval-lift", html)
        self.assertNotIn("eval-pill-row", html)
        self.assertNotIn('<div class="eval-arrow">▶▶</div>', html)

    def test_one_pager_uses_less_marketing_oriented_framing(self) -> None:
        html = _read(PROMO / "one-pager.html")
        readme = _read(PROMO / "README.md")

        self.assertIn("技术摘要", html)
        self.assertNotIn("一份给评委的 60 秒简介", html)
        self.assertNotIn("评委简介", html)
        self.assertNotIn("A4 单页评委简介", readme)
        self.assertNotIn("摆桌上给评委", readme)

    def test_promo_does_not_imply_only_three_total_iterations(self) -> None:
        combined = "\n".join(_read(path) for path in PRIMARY_HTML)

        self.assertNotIn("三轮迭代后", combined)
        self.assertNotIn("三轮收敛过程", combined)
        self.assertNotIn("three iterations", combined)
        self.assertIn("三阶段评测路径", combined)

    def test_promo_eval_copy_names_rubric_and_technical_version_deltas(self) -> None:
        eval_pages = [
            PROMO / "pitch-deck.html",
            PROMO / "landing-page.html",
            PROMO / "one-pager.html",
            PROMO / "xhs-carousel.html",
        ]

        for path in eval_pages:
            text = _read(path)
            for expected in [
                "Rubric",
                "TravelPlanner 四指标",
                "delivery_rate",
                "commonsense_pass",
                "hard_constraint_pass",
                "final_pass",
                "S1-S5",
                "v1 = 裸 Planner 基线",
                "v3 = Planner + AvailabilityProbe + Replanner + IM",
            ]:
                with self.subTest(page=path.name, expected=expected):
                    self.assertIn(expected, text)

            self.assertNotIn("v3 是当前版本", text)
            self.assertNotIn("v3 当前版本", text)

    def test_promo_eval_rubric_explains_metric_meanings(self) -> None:
        eval_pages = [
            PROMO / "pitch-deck.html",
            PROMO / "landing-page.html",
            PROMO / "one-pager.html",
            PROMO / "xhs-carousel.html",
        ]

        for path in eval_pages:
            text = _read(path)
            for expected in [
                "计划非空 + JSON 合法",
                "时间顺序 / 餐时 / 地理常识",
                "预算 / 时段 / 人数 / 忌口",
                "四项同时通过",
            ]:
                with self.subTest(page=path.name, expected=expected):
                    self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
