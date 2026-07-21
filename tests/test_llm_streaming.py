import unittest


class LLMStreamingTest(unittest.TestCase):
    def test_stream_protocol_requires_immediate_status_without_prompt_conflict(self) -> None:
        from src.agents import planner

        self.assertIn("非流式", planner.PLANNER_SYSTEM)
        self.assertNotIn("只输出 JSON，不要任何其他文字", planner.PLANNER_SYSTEM)
        self.assertIn("第一行必须立即输出 status", planner.PLANNER_EVENT_STREAM_PROTOCOL)
        self.assertIn('"event":"status"', planner.PLANNER_EVENT_STREAM_PROTOCOL)

    def test_event_stream_plan_parser_accepts_status_and_final_plan(self) -> None:
        from src.agents.planner import parse_plan_response_text

        text = "\n".join([
            '{"event":"status","text":"正在筛选适合孩子的动物相关地点"}',
            '{"event":"status","text":"正在排除高排队风险餐厅"}',
            '{"event":"final_plan","data":{"persona":"family","area_anchor":"五道营-雍和宫片区","steps":[{"step_index":1,"kind":"depart","poi_name":"返程","start_time":"18:00","duration_min":0,"mode_to_here":"transit","rationale":"返程"}],"fallback_strategies":{},"summary":"测试"}}',
        ])

        parsed = parse_plan_response_text(text)

        self.assertEqual(parsed["summary"], "测试")
        self.assertEqual(parsed["steps"][0]["poi_name"], "返程")

    def test_event_stream_plan_parser_accepts_model_plan_key_variant(self) -> None:
        from src.agents.planner import parse_plan_response_text

        text = "\n".join([
            '{"event":"status","text":"开始测试"}',
            '{"event":"final_plan","plan":{"persona":"family","area_anchor":"五道营-雍和宫片区","steps":[{"step_index":1,"kind":"depart","poi_name":"返程","start_time":"18:00","duration_min":0,"mode_to_here":"transit","rationale":"返程"}],"fallback_strategies":{},"summary":"兼容 plan key"}}',
        ])

        parsed = parse_plan_response_text(text)

        self.assertEqual(parsed["summary"], "兼容 plan key")

    def test_trace_extracts_model_status_events_from_jsonl_stream(self) -> None:
        from src.ui.app import extract_model_status_events

        text = (
            '{"event":"status","text":"正在筛选适合孩子的动物相关地点"}\n'
            '{"event":"status","text":"正在排除高排队风险餐厅"}\n'
            '{"event":"final_plan","data":{"steps":[]}}\n'
        )

        self.assertEqual(
            extract_model_status_events(text),
            [
                "模型：正在筛选适合孩子的动物相关地点",
                "模型：正在排除高排队风险餐厅",
            ],
        )

    def test_planner_emits_progress_events_and_streamed_tokens(self) -> None:
        from src.agents.llm_client import MockLLMClient
        from src.agents.planner import plan
        from src.agents.types import UserPreferences

        tokens: list[str] = []
        events: list[str] = []

        p = plan(
            user_input="今天下午带老婆和 5 岁娃出去玩，别离家太远。",
            persona="family",
            prefs=UserPreferences(
                persona="family",
                party_size=3,
                has_child=True,
                child_age=5,
                budget_per_person=120,
                target_start="14:00",
            ),
            client=MockLLMClient(),
            on_token=tokens.append,
            on_progress=events.append,
            on_stream_event=events.append,
        )

        self.assertGreaterEqual(len(p.steps), 3)
        self.assertGreater(len(tokens), 0)
        streamed = "".join(tokens)
        self.assertIn('"event":"status"', streamed)
        self.assertIn('"steps"', streamed)
        joined_events = "\n".join(events)
        self.assertIn("查询人数", joined_events)
        self.assertIn("查询POI候选", joined_events)
        self.assertIn("调用LLM生成结构化方案", joined_events)
        self.assertIn("校验模型输出", joined_events)
        self.assertIn("模型连接成功", joined_events)

    def test_stream_status_and_plan_share_one_llm_call(self) -> None:
        from src.agents.llm_client import MockLLMClient
        from src.agents.planner import plan
        from src.agents.types import UserPreferences

        class CountingClient(MockLLMClient):
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, *args, **kwargs):
                self.calls += 1
                return super().complete(*args, **kwargs)

        events: list[str] = []
        tokens: list[str] = []
        client = CountingClient()

        plan(
            user_input="带娃下午出去，别太远。",
            persona="family",
            prefs=UserPreferences(persona="family", party_size=3, has_child=True, child_age=5),
            client=client,
            on_token=tokens.append,
            on_progress=events.append,
            on_stream_event=events.append,
        )

        streamed = "".join(tokens)
        self.assertEqual(client.calls, 1)
        self.assertLess(streamed.index('"event":"status"'), streamed.index('"event":"final_plan"'))
        self.assertNotIn("启动模型预分析", "\n".join(events))


if __name__ == "__main__":
    unittest.main()
