"""P0.2 验收：重要场合 → 筛选模式。

来源：USER_RESEARCH_FINDINGS 信号 5（5/5 一致：6 人生日饭只用 BJ-Pal 筛餐厅）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.planner import screen_candidates  # noqa: E402
from agents.preference_mirror import (  # noqa: E402
    detect_party_size,
    detect_screening_mode,
)
from agents.types import UserPreferences  # noqa: E402


KEYWORD_CASES = [
    ("老婆生日带娃带双方父母 6 人吃饭", True),
    ("6 个人聚餐", True),
    ("朋友结婚纪念日饭", True),
    ("家宴老人首次见", True),
    ("8 人去吃烤鸭", True),
    ("周六下午随便吃个饭", False),
    ("两个人下午溜达", False),
    ("3 人喝咖啡", False),
]


def t1_keyword_detection():
    """关键词触发 screening 模式。"""
    print(f"\n[1] 关键词检测：")
    fails = []
    for raw, expected in KEYWORD_CASES:
        got = detect_screening_mode(raw)
        status = "✓" if got == expected else "✗"
        print(f"    {status} '{raw[:30]:30}' → {got} (expect {expected})")
        if got != expected:
            fails.append(raw)
    assert not fails, f"误判：{fails}"
    return len(KEYWORD_CASES)


def t2_party_size_extraction():
    """detect_party_size 抽人数。"""
    cases = [
        ("6 人生日", 6),
        ("两个人", None),  # 数字 in 中文不抽
        ("我们 8 个", 8),
        ("3 人均 ¥100", None),  # "人均"不当作人数
        ("5 个人", None),  # "5 个" without "人"
    ]
    print(f"\n[2] 人数抽取：")
    for raw, expected in cases:
        got = detect_party_size(raw)
        print(f"    '{raw}' → {got} (expect {expected})")
        assert got == expected, f"{raw}: 期望 {expected} 得到 {got}"
    return len(cases)


def t3_screen_candidates_returns_no_plan():
    """筛选模式不返回 plan，只返回 candidates 列表。"""
    result = screen_candidates(
        user_input="老婆生日 6 人家宴",
        persona="family",
        prefs=UserPreferences(persona="family", party_size=6,
                              budget_per_person=300, target_start="18:00"),
        area_anchor="王府井-东单片区",
        category="food",
        top_k=5,
    )
    print(f"\n[3] screen result keys: {list(result.keys())}")
    print(f"    候选数: {len(result.get('candidates', []))}")
    print(f"    decision_hint: {result.get('decision_hint')}")
    assert result["mode"] == "screening"
    assert "candidates" in result
    assert "steps" not in result, "筛选模式不应输出 plan steps"
    assert result.get("decision_hint")
    return len(result["candidates"])


def t4_candidates_have_fit_and_concerns():
    """每家候选必带 fit_reasons + concerns 至少一个。"""
    result = screen_candidates(
        user_input="老婆生日 6 人家宴",
        persona="family",
        prefs=UserPreferences(persona="family", party_size=6,
                              budget_per_person=300, target_start="18:00"),
        area_anchor="王府井-东单片区",
        category="food",
        top_k=5,
    )
    print(f"\n[4] candidate detail (top 3):")
    for c in result["candidates"][:3]:
        print(f"  {c['poi_name']:30} score={c['score']:.3f}")
        print(f"    fit:      {c['fit_reasons']}")
        print(f"    concerns: {c['concerns']}")
        assert isinstance(c["fit_reasons"], list)
        assert isinstance(c["concerns"], list)
        # 至少有一个理由（fit 或 concern）
        assert c["fit_reasons"] or c["concerns"], f"{c['poi_name']} 必须至少有一个 reason"
        assert "score" in c
        assert "red_flags" in c
    return True


def t5_planning_mode_returns_full_plan():
    """对照：planning 模式（不走 screen_candidates）应该输出完整 plan。"""
    from agents.planner import plan as make_plan
    p = make_plan(
        user_input="周六下午随便吃个饭",
        persona="family",
        prefs=UserPreferences(persona="family", party_size=2,
                              budget_per_person=120, target_start="14:00"),
    )
    print(f"\n[5] planning 模式 plan steps={len(p.steps)}")
    assert len(p.steps) >= 3
    return len(p.steps)


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal P0.2 重要场合筛选模式 Tests")
    print("=" * 60)
    suite = [
        ("keyword_detection", t1_keyword_detection),
        ("party_size", t2_party_size_extraction),
        ("screen_no_plan", t3_screen_candidates_returns_no_plan),
        ("fit_and_concerns", t4_candidates_have_fit_and_concerns),
        ("planning_full_plan", t5_planning_mode_returns_full_plan),
    ]
    failed = []
    for name, fn in suite:
        try:
            fn()
        except AssertionError as e:
            failed.append((name, str(e)))
            print(f"    ✗ {e}")
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))
            import traceback; traceback.print_exc()
    print("\n" + "=" * 60)
    if failed:
        print(f"✗ {len(failed)} 项失败")
        for n, m in failed:
            print(f"  - {n}: {m}")
        sys.exit(1)
    print("✓ P0.2 验收 OK")
