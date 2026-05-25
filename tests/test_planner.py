"""W1 D3 验收：Planner 输出结构化 Plan。

默认用 mock client 跑，不依赖网络。
设 BJ_PAL_TEST_LONGCAT=1 同时跑 LongCat 真实调用（消耗 token）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.planner import plan  # noqa: E402
from agents.types import Plan, UserPreferences  # noqa: E402


def t1_plan_family():
    """家庭画像：5 岁娃 + 减脂老婆，预算 ≤ 100，14:00 开始。"""
    p = plan(
        user_input="今天下午带老婆和 5 岁娃出去玩，别离家太远，4 小时左右。老婆减脂，娃喜欢动物。",
        persona="family",
        prefs=UserPreferences(
            persona="family",
            party_size=3,
            has_child=True,
            child_age=5,
            diet_flags=["light_diet"],
            walk_radius_km=1.5,
            budget_per_person=120,
            target_start="14:00",
            duration_hours=4.5,
            raw_input="...",
        ),
    )
    print(f"\n[1] family plan → {len(p.steps)} 步")
    for s in p.steps:
        print(f"    {s.step_index}. [{s.kind:9}] {s.start_time} {s.poi_name:20} "
              f"({s.duration_min}min, {s.mode_to_here})")
        print(f"       {s.rationale[:80]}")
    print(f"    summary: {p.summary}")
    print(f"    fallbacks: {list(p.fallback_strategies.keys())}")
    assert isinstance(p, Plan)
    assert len(p.steps) >= 3, "至少 3 步"
    assert all(s.poi_name for s in p.steps), "每步必须有 poi_name"
    assert all(s.rationale for s in p.steps), "每步必须有 rationale"
    return len(p.steps)


def t2_plan_friends():
    """朋友画像：4 人 2 男 2 女，预算 ≤ 250。"""
    p = plan(
        user_input="跟 4 个朋友周六下午出去玩，2 男 2 女，别太赶，能聊天。",
        persona="friends",
        prefs=UserPreferences(
            persona="friends",
            party_size=4,
            has_child=False,
            walk_radius_km=2.0,
            budget_per_person=250,
            target_start="14:30",
            duration_hours=5.0,
        ),
    )
    print(f"\n[2] friends plan → {len(p.steps)} 步")
    for s in p.steps:
        print(f"    {s.step_index}. [{s.kind:9}] {s.start_time} {s.poi_name:20}")
    assert len(p.steps) >= 3
    assert p.persona == "friends"
    return len(p.steps)


def t3_serialization_roundtrip():
    """Plan ↔ dict 互转无损。"""
    p1 = plan(
        user_input="下午带娃去玩",
        prefs=UserPreferences(persona="family", has_child=True, child_age=5),
    )
    d = p1.to_dict()
    p2 = Plan.from_dict(d)
    assert len(p1.steps) == len(p2.steps)
    assert p1.steps[0].poi_name == p2.steps[0].poi_name
    print(f"\n[3] roundtrip ok ({len(p1.steps)} 步)")
    return len(p1.steps)


def t4_mock_uses_real_pois():
    """mock client 必须从真实候选池里选，不能编 POI。"""
    p = plan(
        user_input="下午去五道营",
        prefs=UserPreferences(persona="family", target_start="14:00"),
        area_anchor="五道营-雍和宫片区",
    )
    # 至少有 1 步 poi_id 非 None 且能在 SQLite 找到
    sys.path.insert(0, str(ROOT / "src"))
    from loader import get_conn
    conn = get_conn()
    real_ids = {row["id"] for row in conn.execute("SELECT id FROM pois").fetchall()}
    conn.close()
    real_step_ids = [s.poi_id for s in p.steps if s.poi_id is not None]
    assert real_step_ids, "至少要有一步含真实 poi_id"
    matched = [pid for pid in real_step_ids if pid in real_ids]
    print(f"\n[4] mock plan：{len(real_step_ids)} 步含 POI id, {len(matched)} 在真实库中")
    assert len(matched) == len(real_step_ids), \
        f"所有 step poi_id 必须真实存在；缺失：{set(real_step_ids) - real_ids}"
    return len(matched)


def t5_longcat_optional():
    """可选：用真实 LongCat 跑一遍。"""
    if not os.environ.get("BJ_PAL_TEST_LONGCAT"):
        print("\n[5] LongCat 真实调用：跳过（设 BJ_PAL_TEST_LONGCAT=1 启用）")
        return None
    os.environ["BJ_PAL_LLM"] = "longcat"
    from agents.llm_client import get_llm_client
    p = plan(
        user_input="周六下午带 5 岁娃在五道营附近转转，老婆减脂",
        prefs=UserPreferences(
            persona="family",
            has_child=True,
            child_age=5,
            diet_flags=["light_diet"],
            budget_per_person=120,
        ),
        client=get_llm_client("longcat"),
    )
    print(f"\n[5] longcat plan → {len(p.steps)} 步")
    for s in p.steps:
        print(f"    {s.step_index}. [{s.kind}] {s.poi_name} @{s.start_time}")
        print(f"       {s.rationale}")
    assert len(p.steps) >= 3
    return len(p.steps)


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal W1 D3 Planner Tests")
    print("=" * 60)
    suite = [
        ("plan_family", t1_plan_family),
        ("plan_friends", t2_plan_friends),
        ("serialization", t3_serialization_roundtrip),
        ("mock_uses_real_pois", t4_mock_uses_real_pois),
        ("longcat_optional", t5_longcat_optional),
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
            print(f"    ✗ {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    print("\n" + "=" * 60)
    if failed:
        print(f"✗ {len(failed)} 项失败")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print("✓ W1 D3 验收 OK")
