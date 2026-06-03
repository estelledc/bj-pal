from types import SimpleNamespace
import inspect
import os
import unittest
from unittest.mock import patch


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

    def test_default_query_uses_best_showcase_case(self) -> None:
        from src.ui import app
        from src.ui import hero

        expected = "今天下午带老婆和 5 岁娃出去玩，别离家太远，4 小时左右。老婆减脂，娃喜欢动物。"
        hero_source = inspect.getsource(hero.render_hero)

        self.assertEqual(app.DEFAULT_SHOWCASE_QUERY, expected)
        self.assertEqual(app.PRESETS["family"]["user_input"], expected)
        self.assertIn(expected, hero_source)
        self.assertNotIn("这周末有半天空", hero_source)

    def test_workspace_keeps_map_persistent(self) -> None:
        from src.ui import app
        from src.ui import map_view
        from src.ui import timeline

        self.assertEqual(app.PRIMARY_WORKSPACE_COLUMNS, ("plan", "map"))
        self.assertEqual(app.SECONDARY_RESULT_TABS, ("发送", "诊断"))
        self.assertEqual(app.DIAGNOSTIC_LABEL, "诊断")
        self.assertEqual(app.AGENT_SKILL_PANEL_LABEL, "Agent 能力目录")
        self.assertEqual(map_view.MAP_VISUALIZATION_CAPTION, "规划结果可视化图")
        self.assertEqual(timeline.DISSENT_BUTTON_LABEL, "换一个")
        self.assertTrue(timeline.DISSENT_BUTTON_USE_CONTAINER_WIDTH)
        self.assertGreaterEqual(timeline.TIMELINE_COLUMN_WEIGHTS[-1], 1.6)

    def test_memory_panel_uses_chinese_display_labels(self) -> None:
        from src.ui import memory_panel

        self.assertEqual(memory_panel.MEMORY_PANEL_EMPTY_TITLE, "记忆（暂无）")
        self.assertEqual(memory_panel.KIND_LABELS["preference"], "偏好")
        self.assertEqual(memory_panel.KIND_LABELS["dislike"], "禁忌")
        self.assertEqual(memory_panel.KIND_LABELS["fact"], "事实")
        self.assertEqual(memory_panel.KIND_LABELS["identity"], "身份")
        self.assertEqual(memory_panel.display_memory_key("diet:no_lactose"), "饮食：乳糖不耐受")
        self.assertEqual(memory_panel.display_memory_key("taste:vinegar_flavor"), "口味：醋味")
        self.assertEqual(memory_panel.display_memory_key("taste:watermelon"), "口味：西瓜")
        self.assertEqual(memory_panel.display_memory_key("risk:urticaria"), "风险：荨麻疹")
        self.assertEqual(memory_panel.display_memory_key("preference:buffet"), "偏好：自助餐")
        self.assertEqual(memory_panel.display_memory_key("diet:no_beef"), "饮食：不吃牛肉")
        self.assertEqual(memory_panel.display_memory_key("preference:pet_friendly"), "偏好：其他偏好")
        self.assertNotIn("未命名", memory_panel.display_memory_key("risk:custom_llm_tag"))
        self.assertNotIn("mention_count", inspect.getsource(memory_panel.render_memory_panel))

    def test_action_buttons_keep_single_line_at_narrow_widths(self) -> None:
        from src.ui import app
        from src.ui import memory_panel
        from src.ui import timeline

        css_source = inspect.getsource(app._inject_product_css)

        self.assertEqual(memory_panel.MEMORY_FORGET_BUTTON_LABEL, "忘记")
        self.assertTrue(memory_panel.MEMORY_FORGET_BUTTON_USE_CONTAINER_WIDTH)
        self.assertGreaterEqual(memory_panel.MEMORY_ROW_COLUMNS[-1], 1.45)
        self.assertTrue(timeline.DISSENT_BUTTON_USE_CONTAINER_WIDTH)
        self.assertGreaterEqual(timeline.TIMELINE_COLUMN_WEIGHTS[-1], 1.6)
        self.assertIn(".stButton > button p", css_source)
        self.assertIn("word-break: keep-all", css_source)
        self.assertIn("overflow-wrap: normal", css_source)

    def test_header_layout_stacks_subtitle_below_title_to_prevent_overlap(self) -> None:
        from src.ui import app

        css_source = inspect.getsource(app._inject_product_css)

        self.assertIn("flex-direction: column", css_source)
        self.assertIn("align-items: flex-start", css_source)
        self.assertIn("white-space: nowrap", css_source)
        self.assertIn("max-width: 560px", css_source)
        self.assertNotIn("justify-content: space-between", css_source)
        self.assertNotIn("text-align: right", css_source)

    def test_runtime_streaming_progress_is_configured(self) -> None:
        from src.ui import app

        main_source = inspect.getsource(app.main)
        dissent_source = inspect.getsource(app._on_user_dissent)

        self.assertGreaterEqual(len(app.PLAN_STREAM_STEPS), 3)
        self.assertGreaterEqual(len(app.REROUTE_STREAM_STEPS), 3)
        self.assertEqual(app.TRACE_WINDOW_TITLE, "模型执行过程")
        self.assertGreaterEqual(app.TRACE_WINDOW_MAX_LINES, 4)
        self.assertIn("正在理解你的偏好", app.PLAN_STREAM_STEPS[0])
        self.assertIn("_run_with_progress_trace", main_source)
        self.assertIn("_run_with_progress_trace", dissent_source)

    def test_planning_status_uses_one_expanded_then_collapsed_block(self) -> None:
        from src.ui import app

        main_source = inspect.getsource(app.main)

        self.assertEqual(app.PLAN_STATUS_LABEL, "正在生成方案")
        self.assertEqual(app.PLAN_POSTCHECK_LABEL, "正在检查排队、天气和商家状态")
        self.assertTrue(app.PLAN_STATUS_EXPANDED_WHILE_RUNNING)
        self.assertFalse(app.PLAN_STATUS_EXPANDED_AFTER_DONE)
        self.assertIn(
            "with st.status(PLAN_STATUS_LABEL, expanded=PLAN_STATUS_EXPANDED_WHILE_RUNNING)",
            main_source,
        )
        self.assertNotIn(
            'with st.status("正在检查排队、天气和商家状态"',
            main_source,
        )
        self.assertIn(
            "expanded=PLAN_STATUS_EXPANDED_AFTER_DONE",
            main_source,
        )

    def test_progress_worker_uses_captured_session_values(self) -> None:
        from src.ui import app

        main_source = inspect.getsource(app.main)

        self.assertIn("current_user_id = st.session_state.user_id", main_source)
        self.assertNotIn("user_id=st.session_state.user_id", main_source)

    def test_progress_trace_window_slides_and_shows_tokens(self) -> None:
        from src.ui import app

        lines = [f"阶段 {i}" for i in range(1, app.TRACE_WINDOW_MAX_LINES + 3)]
        html = app.build_trace_window_html(
            lines,
            token_count=128,
            title="模型执行过程",
            stream_text='{"steps":[{"kind":"meal"',
        )

        self.assertIn("bjpal-trace-window", html)
        self.assertIn("模型执行过程", html)
        self.assertIn("token 估算 128", html)
        self.assertIn("模型输出", html)
        self.assertIn("&quot;steps&quot;", html)
        self.assertNotIn("阶段 1", html)
        self.assertNotIn("阶段 2", html)
        self.assertIn(f"阶段 {app.TRACE_WINDOW_MAX_LINES + 2}", html)

        css_source = inspect.getsource(app._inject_product_css)
        self.assertIn(".bjpal-trace-window", css_source)
        self.assertIn("height:", css_source)
        self.assertIn("overflow: hidden", css_source)

    def test_task_bar_contains_primary_user_controls(self) -> None:
        from src.ui import app

        self.assertEqual(
            app.TASK_BAR_FIELDS,
            ("persona", "area", "budget", "start_time", "duration", "mode", "generate"),
        )
        self.assertEqual(app.SIDEBAR_SECTIONS, ("记忆",))

    def test_runtime_backend_label_supports_dpsk(self) -> None:
        from src.ui import app

        with patch.dict(os.environ, {"BJ_PAL_LLM": "dpsk"}, clear=False):
            self.assertEqual(app.resolve_llm_backend_label(), "DPSK")

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

    def test_manual_memory_capture_uses_llm_intake(self) -> None:
        from src.agents.llm_client import LLMResponse
        from src.agents.user_memory import forget_all, get_preferences
        from src.ui.app import remember_manual_preference

        class Client:
            @property
            def name(self):
                return "manual-memory-test"

            def complete(self, *args, **kwargs):
                return LLMResponse(text="{}", parsed={
                    "area_anchor": "",
                    "poi_name": "",
                    "taste_tags": ["vinegar_flavor"],
                    "scene_tags": [],
                    "risk_tags": ["medical_diet_risk"],
                    "diet_flags": ["no_lactose"],
                    "preference_tags": ["sour_food"],
                    "avoid_tags": ["buffet"],
                    "aspects": [],
                })

        user_id = "u-ui-memory-test"
        try:
            entries = remember_manual_preference(
                user_id,
                "乳糖不耐受，爱吃醋，不想吃自助",
                client=Client(),
            )
            keys = {entry.mem_key for entry in get_preferences(user_id)}

            self.assertGreaterEqual(len(entries), 1)
            self.assertIn("diet:no_lactose", keys)
            self.assertIn("taste:vinegar_flavor", keys)
            self.assertIn("preference:sour_food", keys)
            self.assertIn("avoid:buffet", keys)
        finally:
            forget_all(user_id)


if __name__ == "__main__":
    unittest.main()
