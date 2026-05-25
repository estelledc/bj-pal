"""W2 D4 验收：偏好镜子。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.preference_mirror import (  # noqa: E402
    apply_clarification,
    clarify_preference,
)
from agents.types import UserPreferences  # noqa: E402


def t1_clarify_jianzhi():
    """'减脂' → 应反问低糖/低油。"""
    r = clarify_preference("老婆减脂")
    print(f"\n[1] '老婆减脂' → needs_clarification={r.needs_clarification}")
    print(f"    Q: {r.clarify_question}")
    print(f"    options: {r.options}")
    assert r.needs_clarification is True
    assert "低糖" in r.clarify_question or "低油" in r.clarify_question
    return r.clarify_question


def t2_clarify_kid():
    """'孩子' → 应反问年龄。"""
    r = clarify_preference("带孩子去玩")
    print(f"\n[2] '带孩子去玩' → needs_clarification={r.needs_clarification}")
    print(f"    Q: {r.clarify_question}")
    assert r.needs_clarification is True
    assert "岁" in r.clarify_question or "年龄" in r.clarify_question
    return r.options


def t3_clarify_no_spicy_explicit():
    """'不吃辣' → 已明确，无需追问。"""
    r = clarify_preference("我不吃辣")
    print(f"\n[3] '我不吃辣' → needs_clarification={r.needs_clarification}")
    print(f"    extracted: {r.extracted_constraint}")
    assert r.needs_clarification is False
    assert "no_spicy" in r.extracted_constraint.get("diet_flags", [])
    return r.extracted_constraint


def t4_apply_low_sugar():
    """选 '低糖优先' 后，diet_flags 含 low_sugar。"""
    prefs = UserPreferences(persona="family")
    new_prefs = apply_clarification(prefs, "低糖优先", "老婆减脂")
    print(f"\n[4] 选 '低糖优先' → diet_flags={new_prefs.diet_flags}")
    assert "low_sugar" in new_prefs.diet_flags
    return new_prefs.diet_flags


def t5_apply_kid_age():
    """选 '5 岁' 后，has_child + child_age=5。"""
    prefs = UserPreferences(persona="family")
    new_prefs = apply_clarification(prefs, "3-5 岁", "带孩子去")
    print(f"\n[5] '3-5 岁' → has_child={new_prefs.has_child} age={new_prefs.child_age}")
    assert new_prefs.has_child is True
    assert new_prefs.child_age in [3, 4, 5]
    return new_prefs.child_age


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal W2 D4 Preference Mirror Tests")
    print("=" * 60)
    suite = [
        ("clarify_jianzhi", t1_clarify_jianzhi),
        ("clarify_kid", t2_clarify_kid),
        ("clarify_no_spicy", t3_clarify_no_spicy_explicit),
        ("apply_low_sugar", t4_apply_low_sugar),
        ("apply_kid_age", t5_apply_kid_age),
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
    print("✓ W2 D4 验收 OK")
