"""v2.4 S4：detect_weekday_context — 工作日识别 + 澄清触发。

来源：USER_RESEARCH_FINDINGS 信号 4（4/5：工作日不属于这个 App）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.preference_mirror import detect_weekday_context  # noqa: E402


def t1_weekday_keywords_trigger():
    """工作日关键词应触发 should_clarify=True。"""
    cases = [
        "周一中午有空吗",
        "周三下班后聚一下",
        "工作日下午想溜达",
        "礼拜二午休出去走走",
        "上着班但想约个咖啡",
    ]
    for raw in cases:
        r = detect_weekday_context(raw)
        assert r["should_clarify"], f"漏判: {raw} → {r}"
        assert r["is_weekday_signal"]
        assert r["suggested_clarification"]
    print(f"\n[1] {len(cases)} 个工作日 query 全部正确触发澄清")


def t2_weekend_keywords_no_clarify():
    """周末 query 不触发澄清。"""
    cases = [
        "周六下午带娃出门",
        "周日去逛南锣",
        "周末双休找地方放空",
        "礼拜六下午",
    ]
    for raw in cases:
        r = detect_weekday_context(raw)
        assert not r["should_clarify"], f"误判: {raw} → {r}"
        assert r["is_weekend_signal"]
    print(f"[2] {len(cases)} 个周末 query 全部正确放行")


def t3_mixed_signal_weekend_overrides():
    """周末关键词覆盖工作日（如"周五下班后周末聚"）。"""
    cases = [
        "周五下班后周末聚一下",
        "周一到周日都有空",
        "工作日加周末连着 5 天",
    ]
    for raw in cases:
        r = detect_weekday_context(raw)
        assert r["is_weekday_signal"] and r["is_weekend_signal"]
        assert not r["should_clarify"], f"周末覆盖失败: {raw}"
    print(f"[3] {len(cases)} 个混合 query 周末正确覆盖")


def t4_neutral_query_no_signal():
    """无时间关键词 → 两个 signal 都 False，不触发澄清。"""
    cases = [
        "想找个吃饭的地方",
        "4 个朋友想 hang out",
        "带娃出门",
    ]
    for raw in cases:
        r = detect_weekday_context(raw)
        assert not r["is_weekday_signal"]
        assert not r["is_weekend_signal"]
        assert not r["should_clarify"]
    print(f"[4] {len(cases)} 个无时间信号 query 不触发澄清")


def t5_returns_full_dict_shape():
    """返回值结构完整（agent / UI 依赖此 schema）。"""
    r = detect_weekday_context("周一中午吃饭")
    required = ["is_weekday_signal", "is_weekend_signal", "day_keyword",
                "should_clarify", "suggested_clarification"]
    for k in required:
        assert k in r, f"缺字段 {k}"
    assert r["day_keyword"] == "周一"
    print(f"[5] 返回值 schema 完整：{list(r.keys())}")


if __name__ == "__main__":
    t1_weekday_keywords_trigger()
    t2_weekend_keywords_no_clarify()
    t3_mixed_signal_weekend_overrides()
    t4_neutral_query_no_signal()
    t5_returns_full_dict_shape()
    print("\n所有 S4 验收通过！")
