"""P1.3 验收：老年简化版卡片。

来源：USER_RESEARCH_FINDINGS - 李慧珍场景（投票卡片我真看不懂）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.preference_mirror import detect_has_elderly  # noqa: E402
from agents.types import Plan, Step  # noqa: E402
from tools.mock_message import render_im_card  # noqa: E402


def _make_plan() -> Plan:
    return Plan(
        persona="family",
        area_anchor="王府井",
        steps=[
            Step(step_index=0, poi_name="北京饭店茶座", start_time="14:00",
                 kind="rest", rationale="家庭茶座"),
            Step(step_index=1, poi_name="王府井百货", start_time="15:30",
                 kind="shopping", rationale="逛逛", is_rerouted=True),
        ],
        summary="王府井下午方案 ¥120 / 人",
    )


def t1_keyword_detection():
    """检测老人参与。"""
    cases = [
        ("带外婆出去玩", True),
        ("老婆 + 5 岁娃", False),
        ("妈妈来北京", True),
        ("跟朋友吃饭", False),
        ("接父母逛王府井", True),
        ("退休老人下午茶", True),
    ]
    print(f"\n[1] 老人关键词：")
    for raw, expected in cases:
        got = detect_has_elderly(raw)
        status = "✓" if got == expected else "✗"
        print(f"    {status} '{raw:25}' → {got}")
        assert got == expected
    return len(cases)


def t2_elderly_style_strips_emoji():
    """elderly_friendly 模式去掉 emoji 矩阵。"""
    p = _make_plan()
    elder = render_im_card(p, audience="spouse", style="elderly_friendly")
    default = render_im_card(p, audience="spouse", style="default")
    print(f"\n[2] elderly body:\n{elder.body}")
    print(f"\n    default body:\n{default.body}")
    # elderly 不该含 🔄 emoji
    assert "🔄" not in elder.body
    assert "(已换)" in elder.body or "已换" in elder.body or not any(s.is_rerouted for s in p.steps)
    # default 应当有 🔄
    assert "🔄" in default.body
    return True


def t3_elderly_buttons_simplified():
    """elderly 模式按钮压成 是/否 两个。"""
    p = _make_plan()
    elder = render_im_card(p, audience="spouse", style="elderly_friendly")
    print(f"\n[3] elderly actions: {[a['label'] for a in elder.actions]}")
    labels = [a["label"] for a in elder.actions]
    assert labels == ["是", "否"], f"应为 [是, 否] 实际 {labels}"
    return True


def t4_default_buttons_full():
    """default 模式按钮齐全。"""
    p = _make_plan()
    default = render_im_card(p, audience="spouse", style="default")
    labels = [a["label"] for a in default.actions]
    print(f"\n[4] default actions: {labels}")
    assert "确认" in labels
    assert "换一个" in labels
    return True


def t5_card_carries_style():
    """MessageCard 带 style 字段方便 UI 识别。"""
    p = _make_plan()
    elder = render_im_card(p, audience="spouse", style="elderly_friendly")
    default = render_im_card(p, audience="spouse", style="default")
    assert elder.style == "elderly_friendly"
    assert default.style == "default"
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal P1.3 老年简化版卡片 Tests")
    print("=" * 60)
    suite = [
        ("keyword_detection", t1_keyword_detection),
        ("elderly_strips_emoji", t2_elderly_style_strips_emoji),
        ("elderly_buttons", t3_elderly_buttons_simplified),
        ("default_buttons", t4_default_buttons_full),
        ("style_field", t5_card_carries_style),
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
    print("✓ P1.3 验收 OK")
