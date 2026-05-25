"""v2.5 D2 验收：text_intake 文本抽取 — 多模态首屏后端能力。

参考：docs/V2.4_ITERATION_PLAN.md v2.5 路线（防 GPT-5 抹平窗口）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.text_intake import (  # noqa: E402
    TextIntakeResult,
    extract_from_text,
    merge_into_user_input,
)


def t1_rules_extract_article():
    """公众号片段：五道营 + coffee + dessert + kid_friendly。"""
    article = (
        "周末去了五道营胡同，雍和宫北边那一片现在很出片。"
        "推荐静水StillWater 的咖啡和马卡龙，环境安静适合带娃看绘本，"
        "人均 80 左右不算贵。建议中午去，下午容易排队。"
    )
    r = extract_from_text(article, use_llm=False)
    print(f"\n[1] area={r.area_anchor} taste={r.taste_tags} scene={r.scene_tags} risk={r.risk_tags}")
    assert r.area_anchor == "五道营-雍和宫片区"
    assert "coffee" in r.taste_tags
    assert "dessert" in r.taste_tags
    assert "kid_friendly" in r.scene_tags
    assert "queue_long" in r.risk_tags


def t2_rules_extract_complaint():
    """朋友圈吐槽：三里屯 + 拥挤 + 排队。"""
    text = "今天三里屯逛了一下午，太挤了，火锅店还排队 1 小时"
    r = extract_from_text(text, use_llm=False)
    print(f"[2] area={r.area_anchor} taste={r.taste_tags} risk={r.risk_tags}")
    assert r.area_anchor == "三里屯片区"
    assert "spicy" in r.taste_tags
    assert "queue_long" in r.risk_tags
    assert "crowded" in r.risk_tags


def t3_empty_input():
    """空输入 → source=empty。"""
    for raw in ["", "   ", "\n\n"]:
        r = extract_from_text(raw, use_llm=False)
        assert r.source == "empty"
        assert r.is_empty()
    print(f"[3] 空输入处理 OK")


def t4_non_beijing_no_area():
    """非北京文本 → area_anchor=空。"""
    r = extract_from_text("上海徐汇区有家咖啡店不错", use_llm=False)
    print(f"[4] non-bj area={r.area_anchor!r} taste={r.taste_tags}")
    assert r.area_anchor == ""
    # taste 仍可命中（咖啡是通用关键词），但片区为空告诉下游降级


def t5_book_quote_extracts_poi():
    """书名号包围识别为 poi_name。"""
    r = extract_from_text("听说《静水居》挺好的", use_llm=False)
    print(f"[5] poi={r.poi_name!r}")
    assert r.poi_name == "静水居"


def t6_llm_path_via_mock():
    """use_llm=True 走 mock LLM 路径，结果可解析。"""
    text = "周日带娃去五道营雍和宫附近遛弯，找个安静的咖啡店"
    r = extract_from_text(text, use_llm=True)
    print(f"[6] llm path: source={r.source} area={r.area_anchor} taste={r.taste_tags}")
    assert r.source == "llm"
    assert r.area_anchor == "五道营-雍和宫片区"


def t7_merge_into_user_input():
    """merge_into_user_input 拼接结构正确。"""
    intake = TextIntakeResult(
        area_anchor="五道营-雍和宫片区",
        poi_name="静水",
        taste_tags=["coffee"],
        scene_tags=["quiet"],
        risk_tags=["queue_long"],
    )
    merged = merge_into_user_input("4 人下午找地方", intake, intent_hint="想避开嘈杂")
    print(f"[7] merged contains:\n{merged}")
    assert "五道营" in merged
    assert "静水" in merged
    assert "coffee" in merged
    assert "queue_long" in merged
    assert "想避开嘈杂" in merged


def t8_merge_skips_empty_intake():
    """空 intake 不污染原 query。"""
    empty = TextIntakeResult(source="empty")
    out = merge_into_user_input("原始 query", empty)
    assert out == "原始 query"
    print(f"[8] 空 intake 跳过 merge")


def t9_planner_sees_augmented_query():
    """e2e: text_intake → merge → planner 接收时含外部信号。

    用 planner 的 mock 路径跑一遍，确认 user_input 被增强后
    plan 能正常生成（不崩、不空）。
    """
    from agents.planner import plan
    from agents.types import UserPreferences

    base = "4 个朋友周六下午雍和宫附近"
    intake = extract_from_text(
        "听说静水StillWater 咖啡好喝，环境安静",
        use_llm=False,
    )
    augmented = merge_into_user_input(base, intake)
    assert "静水" in augmented or "coffee" in augmented

    prefs = UserPreferences(persona="friends", raw_input=augmented,
                             target_start="14:00", duration_hours=4.0)
    p = plan(user_input=augmented, persona="friends", prefs=prefs)
    assert len(p.steps) >= 4
    print(f"[9] e2e: plan 跑通 ({len(p.steps)} 步)，augmented_input 含外部信号")


if __name__ == "__main__":
    t1_rules_extract_article()
    t2_rules_extract_complaint()
    t3_empty_input()
    t4_non_beijing_no_area()
    t5_book_quote_extracts_poi()
    t6_llm_path_via_mock()
    t7_merge_into_user_input()
    t8_merge_skips_empty_intake()
    t9_planner_sees_augmented_query()
    print("\n所有 v2.5 D2 text_intake 验收通过！")
