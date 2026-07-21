"""Privacy-minimized local tracing tests.

覆盖：
- backend=off 完全 no-op（不写文件、不抛错）
- backend=jsonl 把 span 写到不可反推 session 的 hashed path
- 嵌套 span 正确建立 parent_span_id 关系
- session/user/decision/error 正文不进入导出文件
- 装饰器 @traced 工作
- planner.plan / planner_tot.plan_tot 在 jsonl 模式产出多条 span
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _reset(tmp_dir: str | None = None):
    """重置 backend 单例 + 清 env，让每个 test 互相隔离。"""
    if tmp_dir:
        os.environ["BJ_PAL_TRACE"] = "jsonl"
    else:
        os.environ["BJ_PAL_TRACE"] = "off"
    from agents import tracing
    tracing.reset_backend_for_tests()
    # 重新指向 tmp_dir
    if tmp_dir:
        tracing._default_jsonl_dir = lambda: Path(tmp_dir)
    return tracing


def _trace_path(tracing, root: str, session_id: str) -> Path:
    return Path(root) / tracing.hashed_trace_filename(session_id)


def test_off_is_noop():
    os.environ["BJ_PAL_TRACE"] = "off"
    from agents import tracing
    tracing.reset_backend_for_tests()
    with tracing.trace_span("foo", attrs={"k": 1}) as sp:
        sp.set_attribute("x", 2)
        assert sp.span_id == ""  # off 模式空 id
    print("OK off")


def test_jsonl_writes_spans():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BJ_PAL_TRACE"] = "jsonl"
        from agents import tracing
        tracing.reset_backend_for_tests()
        tracing._default_jsonl_dir = lambda: Path(tmp)
        tracing.set_session("test-session-1")

        with tracing.trace_span("outer", attrs={"a": 1}):
            with tracing.trace_span("inner1"):
                pass
            with tracing.trace_span("inner2"):
                pass

        path = _trace_path(tracing, tmp, "test-session-1")
        assert path.exists(), f"应该写到 {path}"
        lines = [json.loads(l) for l in path.read_text().splitlines()]
        assert len(lines) == 3
        # 嵌套关系检查
        names = [l["name"] for l in lines]
        assert "outer" in names
        assert "inner1" in names
        assert "inner2" in names
        outer = next(l for l in lines if l["name"] == "outer")
        inner1 = next(l for l in lines if l["name"] == "inner1")
        inner2 = next(l for l in lines if l["name"] == "inner2")
        assert outer["parent_span_id"] is None
        assert inner1["parent_span_id"] == outer["span_id"]
        assert inner2["parent_span_id"] == outer["span_id"]
        # 只保留 session 摘要，不输出原值。
        expected_hash = hashlib.sha256(b"test-session-1").hexdigest()
        assert all(l["session_id_sha256"] == expected_hash for l in lines)
        assert "test-session-1" not in path.read_text()
        # trace_id 同一棵
        assert outer["trace_id"] == inner1["trace_id"] == inner2["trace_id"]
    print("OK jsonl basic")


def test_decorator_traced():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BJ_PAL_TRACE"] = "jsonl"
        from agents import tracing
        tracing.reset_backend_for_tests()
        tracing._default_jsonl_dir = lambda: Path(tmp)
        tracing.set_session("test-deco")

        @tracing.traced("my_func")
        def f(x):
            return x * 2

        assert f(5) == 10
        path = _trace_path(tracing, tmp, "test-deco")
        lines = [json.loads(l) for l in path.read_text().splitlines()]
        assert any(l["name"] == "my_func" for l in lines)
    print("OK decorator")


def test_error_status():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BJ_PAL_TRACE"] = "jsonl"
        from agents import tracing
        tracing.reset_backend_for_tests()
        tracing._default_jsonl_dir = lambda: Path(tmp)
        tracing.set_session("test-err")

        try:
            with tracing.trace_span("bad"):
                raise ValueError("boom")
        except ValueError:
            pass

        path = _trace_path(tracing, tmp, "test-err")
        lines = [json.loads(l) for l in path.read_text().splitlines()]
        bad = next(l for l in lines if l["name"] == "bad")
        assert bad["status"] == "error"
        assert bad["attributes"]["error.type"] == "ValueError"
        assert "boom" not in path.read_text()
    print("OK error status")


def test_planner_emits_trace():
    """端到端：跑一次 mock plan()，检查关键 span 都在。"""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BJ_PAL_LLM"] = "mock"
        os.environ["BJ_PAL_TRACE"] = "jsonl"
        from agents import tracing
        tracing.reset_backend_for_tests()
        tracing._default_jsonl_dir = lambda: Path(tmp)
        tracing.set_session("test-planner")

        from agents.planner import plan
        from agents.types import UserPreferences
        prefs = UserPreferences(persona="family", party_size=3,
                                  has_child=True, walk_radius_km=1.5,
                                  budget_per_person=120,
                                  target_start="14:00", duration_hours=4.5)
        p = plan(user_input="带娃出门",
                  persona="family", prefs=prefs,
                  area_anchor="五道营-雍和宫片区")
        assert p is not None and len(p.steps) >= 4

        path = _trace_path(tracing, tmp, "test-planner")
        lines = [json.loads(l) for l in path.read_text().splitlines()]
        names = {l["name"] for l in lines}
        assert "planner.plan" in names, f"got {names}"
        assert "planner.collect_data" in names
        assert "llm.mock.complete" in names
        # planner.plan 应该是 root（parent_span_id=None）
        root = next(l for l in lines if l["name"] == "planner.plan")
        assert root["parent_span_id"] is None
        # llm.mock.complete 应该挂在 planner.plan 下（祖先）
        llm = next(l for l in lines if l["name"] == "llm.mock.complete")
        # 同一 trace_id 即可
        assert llm["trace_id"] == root["trace_id"]
    print("OK planner end-to-end")


def test_plan_tot_emits_branches():
    """ToT 三分支应该写出 3 条 tot.branch + 1 条 planner.plan_tot。"""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BJ_PAL_LLM"] = "mock"
        os.environ["BJ_PAL_TRACE"] = "jsonl"
        from agents import tracing
        tracing.reset_backend_for_tests()
        tracing._default_jsonl_dir = lambda: Path(tmp)
        tracing.set_session("test-tot")

        from agents.planner_tot import plan_tot
        from agents.types import UserPreferences
        prefs = UserPreferences(persona="family", has_child=True,
                                  walk_radius_km=1.5, budget_per_person=120,
                                  target_start="14:00", duration_hours=4.5)
        best, branches = plan_tot(
            user_input="带娃出门",
            persona="family", prefs=prefs,
            area_anchor="五道营-雍和宫片区",
            max_workers=1,  # serial，确保都在同一 ContextVar 链
        )
        assert best is not None
        path = _trace_path(tracing, tmp, "test-tot")
        lines = [json.loads(l) for l in path.read_text().splitlines()]
        names = [l["name"] for l in lines]
        assert "planner.plan_tot" in names
        assert names.count("tot.branch") == 3
        # 每个 branch 都应当有 score attr
        for l in lines:
            if l["name"] == "tot.branch":
                assert "bj_pal.score" in l["attributes"] or l["status"] == "error"
    print("OK plan_tot trace")


def test_jsonl_privacy_projection_and_health():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BJ_PAL_TRACE"] = "jsonl"
        from agents import tracing
        tracing.reset_backend_for_tests()
        tracing._default_jsonl_dir = lambda: Path(tmp)
        private_session = "session-private-marker"
        tracing.set_session(private_session)

        with tracing.trace_span(
            "llm.dpsk.complete",
            attrs={
                "user_id": "user-private-marker",
                "decision": "decision-private-marker",
                "prompt": "prompt-private-marker",
                "input_tokens": 11,
                "output_tokens": 7,
            },
        ):
            pass

        path = _trace_path(tracing, tmp, private_session)
        raw = path.read_text()
        payload = json.loads(raw)
        assert "session-private-marker" not in raw
        assert "user-private-marker" not in raw
        assert "decision-private-marker" not in raw
        assert "prompt-private-marker" not in raw
        assert payload["attributes"]["gen_ai.operation.name"] == "chat"
        assert payload["attributes"]["gen_ai.provider.name"] == "deepseek"
        assert payload["attributes"]["gen_ai.usage.input_tokens"] == 11
        assert payload["attributes"]["gen_ai.usage.output_tokens"] == 7
        status = tracing.trace_export_status().to_dict()
        assert status["state"] == "healthy"
        assert status["content_capture_enabled"] is False
        assert status["exported_span_count"] == 1
        assert status["dropped_attribute_count"] == 3


def test_invalid_otlp_configuration_degrades_without_jsonl_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BJ_PAL_TRACE"] = "otlp"
        os.environ.pop("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", None)
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        from agents import tracing
        tracing.reset_backend_for_tests()
        tracing._default_jsonl_dir = lambda: Path(tmp)
        with tracing.trace_span("planner.plan"):
            pass
        status = tracing.trace_export_status().to_dict()
        assert status["backend"] == "otlp"
        assert status["state"] == "degraded"
        assert status["last_error_code"] == "missing_endpoint"
        assert list(Path(tmp).iterdir()) == []


def test_jsonl_initialization_failure_does_not_break_business(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied")
    os.environ["BJ_PAL_TRACE"] = "jsonl"
    from agents import tracing

    tracing.reset_backend_for_tests()
    original = tracing._default_jsonl_dir
    tracing._default_jsonl_dir = lambda: blocked
    try:
        with tracing.trace_span("planning.execute"):
            business_result = "succeeded"
        status = tracing.trace_export_status().to_dict()
    finally:
        tracing._default_jsonl_dir = original
        tracing.reset_backend_for_tests()
    assert business_result == "succeeded"
    assert status["backend"] == "jsonl"
    assert status["state"] == "degraded"
    assert status["last_error_code"] == "jsonl_initialization_failed"


if __name__ == "__main__":
    test_off_is_noop()
    test_jsonl_writes_spans()
    test_decorator_traced()
    test_error_status()
    test_planner_emits_trace()
    test_plan_tot_emits_branches()
    test_jsonl_privacy_projection_and_health()
    test_invalid_otlp_configuration_degrades_without_jsonl_fallback()
    with tempfile.TemporaryDirectory() as tmp:
        test_jsonl_initialization_failure_does_not_break_business(Path(tmp))
    print("\nOK test_tracing 9/9")
