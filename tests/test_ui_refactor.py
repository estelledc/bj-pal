from types import SimpleNamespace
import inspect
import unittest


class UIRefactorTest(unittest.TestCase):
    def test_product_positioning_matches_weekend_leisure_scope(self) -> None:
        from src.ui import app

        self.assertEqual(app.PRODUCT_PAGE_TITLE, "BJ-Pal · 周末闲时活动规划")
        self.assertEqual(app.PRODUCT_KICKER, "BJ-Pal · 北京周末闲时规划")
        self.assertEqual(app.PRODUCT_HEADLINE, "把周末半天，排成一条能出发的路线")
        self.assertIn("3-5 小时闲时出行", app.PRODUCT_SUBTITLE)
        self.assertNotIn("下午活动管家", app.PRODUCT_HEADLINE)

    def test_legacy_hero_copy_uses_weekend_leisure_positioning(self) -> None:
        from src.ui import hero

        hero_source = inspect.getsource(hero.render_hero)

        self.assertIn("周末闲时活动规划", hero_source)
        self.assertNotIn("北京下午活动管家", hero_source)

    def test_workspace_keeps_map_persistent(self) -> None:
        from src.ui import app

        self.assertEqual(app.PRIMARY_WORKSPACE_COLUMNS, ("plan", "map"))
        self.assertEqual(app.SECONDARY_RESULT_TABS, ("发送", "补充材料", "诊断"))
        self.assertEqual(app.DIAGNOSTIC_LABEL, "诊断")

    def test_task_bar_contains_primary_user_controls(self) -> None:
        from src.ui import app

        self.assertEqual(
            app.TASK_BAR_FIELDS,
            ("persona", "area", "budget", "start_time", "duration", "mode", "generate"),
        )
        self.assertEqual(app.SIDEBAR_SECTIONS, ("演示开关", "记忆与校准"))

    def test_duration_control_overrides_preset_preferences(self) -> None:
        from src.ui import app

        prefs = app.build_user_preferences(
            app.PRESETS["family"],
            budget=180,
            target_start="13:30",
            duration_hours=3.5,
            raw_input="周末半天带娃出门",
        )

        self.assertEqual(prefs.budget_per_person, 180)
        self.assertEqual(prefs.target_start, "13:30")
        self.assertEqual(prefs.duration_hours, 3.5)
        self.assertEqual(prefs.raw_input, "周末半天带娃出门")

    def test_area_control_supports_manual_input(self) -> None:
        from src.ui import app

        self.assertEqual(app.AREA_SELECT_OPTIONS[-1], app.CUSTOM_AREA_OPTION)
        self.assertEqual(
            app.resolve_area_input(
                app.CUSTOM_AREA_OPTION,
                "  798 艺术区  ",
                fallback_area="五道营-雍和宫片区",
            ),
            "798 艺术区",
        )
        self.assertEqual(
            app.resolve_area_input(
                app.CUSTOM_AREA_OPTION,
                "   ",
                fallback_area="五道营-雍和宫片区",
            ),
            "五道营-雍和宫片区",
        )
        self.assertEqual(
            app.resolve_area_input(
                "王府井-东单片区",
                "望京",
                fallback_area="五道营-雍和宫片区",
            ),
            "王府井-东单片区",
        )

    def test_build_plan_snapshot_counts_only_real_stops(self) -> None:
        from src.ui.app import build_plan_snapshot

        plan = SimpleNamespace(
            steps=[
                SimpleNamespace(kind="depart", travel_time_min=0, is_rerouted=False),
                SimpleNamespace(kind="culture", travel_time_min=12, is_rerouted=False),
                SimpleNamespace(kind="meal", travel_time_min=8, is_rerouted=True),
                SimpleNamespace(kind="rest", travel_time_min=5, is_rerouted=False),
            ]
        )
        events = [SimpleNamespace(reason="queue")]

        snapshot = build_plan_snapshot(plan, events)

        self.assertEqual(snapshot["stop_count"], 3)
        self.assertEqual(snapshot["travel_minutes"], 25)
        self.assertEqual(snapshot["reroute_count"], 1)
        self.assertEqual(snapshot["travel_label"], "25 分钟路上")

    def test_reroute_memory_collects_plan_and_event_pois(self) -> None:
        from src.ui.app import collect_reroute_memory_names

        plan = SimpleNamespace(
            steps=[
                SimpleNamespace(kind="meal", poi_name="当前餐厅"),
                SimpleNamespace(kind="depart", poi_name="返程"),
                SimpleNamespace(kind="rest", poi_name="当前咖啡"),
            ]
        )
        events = [
            SimpleNamespace(failed_poi_name="初始餐厅", replacement_poi_name="当前餐厅"),
            SimpleNamespace(failed_poi_name="旧咖啡", replacement_poi_name=None),
        ]

        names = collect_reroute_memory_names(plan, events)

        self.assertEqual(
            names,
            {"当前餐厅", "当前咖啡", "初始餐厅", "旧咖啡"},
        )


if __name__ == "__main__":
    unittest.main()
