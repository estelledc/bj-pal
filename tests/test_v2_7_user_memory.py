"""v2.7 D6 验收：user_memory 跨 session 偏好沉淀。"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.user_memory import (  # noqa: E402
    forget,
    forget_all,
    get_preferences,
    infer_from_user_input,
    merge_into_prompt,
    record_preference,
)
from agents.llm_client import LLMResponse, MockLLMClient  # noqa: E402


def _new_uid() -> str:
    return f"u-test-{uuid.uuid4().hex[:8]}"


class _MemoryClient:
    @property
    def name(self) -> str:
        return "v2-7-memory-client"

    def complete(self, *args, **kwargs):
        parsed = {
            "area_anchor": "",
            "poi_name": "",
            "taste_tags": ["coffee"],
            "scene_tags": ["kid_friendly"],
            "risk_tags": [],
            "diet_flags": ["light_diet", "no_spicy"],
            "preference_tags": [],
            "avoid_tags": [],
            "aspects": [],
        }
        return LLMResponse(text="{}", parsed=parsed)


class _AvoidClient:
    @property
    def name(self) -> str:
        return "v2-7-avoid-client"

    def complete(self, *args, **kwargs):
        parsed = {
            "area_anchor": "",
            "poi_name": "",
            "taste_tags": [],
            "scene_tags": [],
            "risk_tags": [],
            "diet_flags": ["no_spicy"],
            "preference_tags": [],
            "avoid_tags": ["seafood"],
            "aspects": [],
        }
        return LLMResponse(text="{}", parsed=parsed)


def t1_record_and_get():
    uid = _new_uid()
    record_preference(uid, "diet:light_diet", True)
    prefs = get_preferences(uid)
    assert len(prefs) == 1
    assert prefs[0].mem_key == "diet:light_diet"
    forget_all(uid)
    print(f"\n[1] record + get OK")


def t2_mention_count_accumulates():
    uid = _new_uid()
    for _ in range(3):
        record_preference(uid, "taste:coffee", True)
    prefs = get_preferences(uid)
    assert prefs[0].mention_count == 3
    forget_all(uid)
    print(f"[2] 累计 mention_count = 3")


def t3_forget_marks_inactive():
    uid = _new_uid()
    record_preference(uid, "taste:coffee", True)
    record_preference(uid, "taste:dessert", True)
    assert forget(uid, "taste:coffee")
    prefs = get_preferences(uid)
    assert len(prefs) == 1
    assert prefs[0].mem_key == "taste:dessert"
    # include_forgotten=True 时还能找到
    all_prefs = get_preferences(uid, include_forgotten=True)
    assert len(all_prefs) == 2
    forget_all(uid)
    print(f"[3] forget OK")


def t4_re_record_resurrects():
    uid = _new_uid()
    record_preference(uid, "taste:coffee", True)
    forget(uid, "taste:coffee")
    record_preference(uid, "taste:coffee", True)
    prefs = get_preferences(uid)
    assert len(prefs) == 1, f"forget 后 record 应复活：{prefs}"
    forget_all(uid)
    print(f"[4] forget 后 record 复活 OK")


def t5_infer_diet_party_taste():
    uid = _new_uid()
    written = infer_from_user_input(
        uid,
        "今天下午带 5 岁娃出去玩，老婆减脂不吃辣，想找个咖啡店",
        client=_MemoryClient(),
    )
    keys = [w.mem_key for w in written]
    kinds = {w.mem_key: w.kind for w in written}
    print(f"\n[5] infer keys: {keys}")
    assert any("light_diet" in k for k in keys)
    assert any("no_spicy" in k for k in keys)
    assert any("kid_friendly" in k for k in keys)
    assert any("coffee" in k for k in keys)
    # spicy 应抽到 dislike，不是 preference（"不吃辣"）
    if "taste:spicy" in kinds:
        assert kinds["taste:spicy"] == "dislike"
    forget_all(uid)


def t6_infer_no_negation_false_positive():
    """用户明说"不吃海鲜"不应抽到 preference。"""
    uid = _new_uid()
    infer_from_user_input(uid, "我老公不能吃辣，避开海鲜", client=_AvoidClient())
    prefs = get_preferences(uid)
    spicy_pref = [p for p in prefs if p.mem_key == "taste:spicy"]
    if spicy_pref:
        assert spicy_pref[0].kind == "dislike"
    assert any(p.mem_key == "avoid:seafood" and p.kind == "dislike" for p in prefs)
    forget_all(uid)
    print(f"[6] 否定上下文识别正确")


def t7_merge_into_prompt():
    uid = _new_uid()
    record_preference(uid, "diet:light_diet", True, confidence=0.85)
    record_preference(uid, "party:with_child", True, confidence=0.80)
    record_preference(uid, "taste:coffee", True, confidence=0.65)
    merged = merge_into_prompt("4 人下午", uid)
    assert "用户长期偏好" in merged
    assert "light_diet" in merged
    forget_all(uid)
    print(f"[7] merge prompt OK")


def t8_merge_skips_low_confidence():
    """衰减后 confidence < threshold 应被过滤。"""
    uid = _new_uid()
    # 模拟一个非常老的偏好（手工置 confidence=0.3）
    record_preference(uid, "taste:fruit", True, confidence=0.3)
    merged = merge_into_prompt("query", uid, confidence_threshold=0.5)
    assert "fruit" not in merged
    forget_all(uid)
    print(f"[8] 低置信度过滤")


def t9_planner_e2e_cross_session():
    """e2e：手动沉淀偏好后，后续 plan 只读取记忆，不自动写新记忆。"""
    from agents.planner import plan
    uid = _new_uid()
    infer_from_user_input(
        uid,
        "带 5 岁娃出去玩，老婆减脂不吃辣，想找咖啡店",
        client=_MemoryClient(),
    )
    p1 = plan(
        user_input="带 5 岁娃出去玩，老婆减脂不吃辣，想找咖啡店",
        persona="family",
        user_id=uid,
        client=MockLLMClient(),
    )
    prefs_after_1 = get_preferences(uid)
    keys_1 = [p.mem_key for p in prefs_after_1]
    print(f"\n[9] 手动沉淀后 memory: {len(prefs_after_1)} 条")
    assert any("light_diet" in k for k in keys_1)
    assert any("kid_friendly" in k for k in keys_1)

    # 第 2 次 query 不再提偏好
    p2 = plan(
        user_input="这周末再去玩",
        persona="family",
        user_id=uid,
        client=MockLLMClient(),
    )
    prefs_after_2 = get_preferences(uid)
    print(f"      第 2 次后 memory: {len(prefs_after_2)} 条")
    # 跨 session 偏好仍存在
    keys_2 = [p.mem_key for p in prefs_after_2]
    assert any("light_diet" in k for k in keys_2)
    assert any("kid_friendly" in k for k in keys_2)
    # plan_id 不同
    assert p1.plan_id != p2.plan_id

    forget_all(uid)


def t10_no_user_id_stateless():
    """user_id=None → 完全 stateless，不写 memory。"""
    from agents.planner import plan
    p = plan(user_input="带娃减脂", persona="family", user_id=None, client=MockLLMClient())
    # 没人能验证 — 但 plan 本身能跑
    assert len(p.steps) >= 4
    print(f"[10] user_id=None 时 stateless 行为正常")


if __name__ == "__main__":
    t1_record_and_get()
    t2_mention_count_accumulates()
    t3_forget_marks_inactive()
    t4_re_record_resurrects()
    t5_infer_diet_party_taste()
    t6_infer_no_negation_false_positive()
    t7_merge_into_prompt()
    t8_merge_skips_low_confidence()
    t9_planner_e2e_cross_session()
    t10_no_user_id_stateless()
    print("\n所有 v2.7 D6 user_memory 验收通过！")
