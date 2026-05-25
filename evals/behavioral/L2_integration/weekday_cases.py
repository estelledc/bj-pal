"""L2 weekday detection 5 case — 跨场景验证 detect_weekday_context。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.preference_mirror import detect_weekday_context  # noqa: E402


def _runner_factory(query: str, expect_clarify: bool, expect_keyword: str = ""):
    def _r() -> dict:
        t0 = time.perf_counter()
        result = detect_weekday_context(query)
        elapsed = int((time.perf_counter() - t0) * 1000)
        ok_clarify = result["should_clarify"] == expect_clarify
        ok_kw = (not expect_keyword) or result["day_keyword"] == expect_keyword
        return {
            "pass": ok_clarify and ok_kw,
            "observed": {"detected": result, "expected_clarify": expect_clarify,
                         "expected_keyword": expect_keyword},
            "latency_ms": elapsed,
        }
    return _r


CASES = [
    {
        "name": "w1_weekday_lunch",
        "capability": "weekday_context",
        "description": "周一中午临时约饭 → 必澄清",
        "runner": _runner_factory("周一中午有空吗 一起吃个饭", True, "周一"),
    },
    {
        "name": "w2_weekend_overrides",
        "capability": "weekday_context",
        "description": "「周五下班后周末聚」周末覆盖工作日 → 不澄清",
        "runner": _runner_factory("周五下班后周末聚一下", False, "周五"),
    },
    {
        "name": "w3_office_hour_signal",
        "capability": "weekday_context",
        "description": "「上着班但想约咖啡」工作中 → 必澄清",
        "runner": _runner_factory("上着班但想约个咖啡", True, "上着班"),
    },
    {
        "name": "w4_holiday_no_clarify",
        "capability": "weekday_context",
        "description": "「假期」周末关键词 → 不澄清",
        "runner": _runner_factory("假期想找个地方放空", False, "假期"),
    },
    {
        "name": "w5_neutral_query",
        "capability": "weekday_context",
        "description": "中性 query 无时间词 → 不澄清",
        "runner": _runner_factory("4 人想找地方吃饭", False, ""),
    },
]
