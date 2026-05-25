"""v2 改 2 验收：群发投票场景。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.planner import plan as make_plan  # noqa: E402
from agents.replanner import replan_step  # noqa: E402
from agents.types import UserPreferences  # noqa: E402
from tools.availability_probe import user_dissent_probe  # noqa: E402
from tools.mock_message import (  # noqa: E402
    DEMO_FRIEND_GROUP,
    broadcast_to_group,
    render_im_card,
    simulate_group_responses,
)
from tools.types import POI  # noqa: E402


def t1_broadcast_to_4():
    """broadcast 给 4 人各 1 张卡片。"""
    p = make_plan(
        user_input="周六下午跟朋友 4 人玩",
        prefs=UserPreferences(persona="friends", party_size=4,
                              budget_per_person=250, target_start="14:30",
                              walk_radius_km=2.0),
    )
    card = render_im_card(p, audience="friend")
    results = broadcast_to_group(card, DEMO_FRIEND_GROUP)
    print(f"\n[1] broadcast → {len(results)} 张卡片")
    for r in results:
        print(f"    {r.contact} delivered={r.delivered} msg_id={r.message_id}")
    assert len(results) == 4
    assert all(r.delivered for r in results)
    return len(results)


def t2_force_one_dissent():
    """force_one_dissent=True 必有 1 人否决。"""
    p = make_plan(
        user_input="周六下午跟朋友 4 人玩，去吃京兆尹",
        prefs=UserPreferences(persona="friends", party_size=4,
                              budget_per_person=300, target_start="14:30"),
    )
    responses = simulate_group_responses(p, DEMO_FRIEND_GROUP,
                                         force_one_dissent=True, seed=42)
    print(f"\n[2] 4 人响应：")
    for r in responses:
        emoji = {"confirmed": "✅", "rejected": "❌", "waiting": "⏳", "no_reply": "?"}[r.status]
        print(f"    {emoji} {r.avatar} {r.contact:5} ({r.reply_at_ms}ms) "
              f"reason={r.rejection_reason or '-':12} '{r.reply_text}'")
    rejected = [r for r in responses if r.status == "rejected"]
    assert len(rejected) >= 1, "force_one_dissent=True 应至少 1 人否决"
    return len(rejected)


def t3_dissent_triggers_replan():
    """1 人否决 → user_dissent_probe → replan_step。"""
    p = make_plan(
        user_input="周六下午带朋友吃京兆尹",
        prefs=UserPreferences(persona="friends", party_size=4,
                              budget_per_person=300, target_start="14:30"),
    )
    responses = simulate_group_responses(p, DEMO_FRIEND_GROUP, seed=42)
    rejected = [r for r in responses if r.status == "rejected"]
    if not rejected:
        print("\n[3] 无否决，跳过")
        return 0

    # 找方案里第一个 meal step（被反感的多半是它）
    target_step = next((i for i, s in enumerate(p.steps) if s.kind == "meal"), 0)
    target_poi_name = p.steps[target_step].poi_name
    poi = POI(id="poi-x", name=target_poi_name, category_lv1="餐饮服务",
              category_lv2=None, category_lv3=None, typecode=None,
              district=None, business_area=None, address=None,
              longitude=None, latitude=None, rating=4.5, avg_price=200,
              open_time=None, phone=None, photos=[])
    probe_r = user_dissent_probe(poi, party_size=4, target_time="15:00",
                                  reason_text=rejected[0].reply_text)
    new_plan, event = replan_step(p, target_step, probe_r,
                                  prefs=UserPreferences(persona="friends",
                                                         budget_per_person=250))
    print(f"\n[3] dissent → replan: {target_poi_name} → {event.replacement_poi_name}")
    print(f"    rationale: {new_plan.steps[target_step].rationale[:100]}")
    print(f"    reroute_reason: {new_plan.steps[target_step].reroute_reason}")
    assert new_plan.steps[target_step].is_rerouted
    assert new_plan.steps[target_step].reroute_reason == "user_dissent"
    return event.replacement_poi_name


def t4_responses_have_status_distribution():
    """4 人响应应有合理状态分布（confirmed/rejected/waiting）。"""
    p = make_plan(
        user_input="周六下午",
        prefs=UserPreferences(persona="friends", party_size=4),
    )
    # 多次跑统计
    from collections import Counter
    statuses = Counter()
    for seed in range(20):
        responses = simulate_group_responses(p, DEMO_FRIEND_GROUP, seed=seed)
        for r in responses:
            statuses[r.status] += 1
    print(f"\n[4] 20 轮 × 4 人 = 80 个响应状态分布：")
    for s, n in statuses.most_common():
        print(f"    {s:10} {n}")
    assert statuses["confirmed"] >= 30
    assert statuses["rejected"] >= 15  # force_one_dissent 每轮 1 否决
    return statuses


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal v2 改 2 Broadcast Tests")
    print("=" * 60)
    suite = [
        ("broadcast_4", t1_broadcast_to_4),
        ("force_dissent", t2_force_one_dissent),
        ("dissent_triggers_replan", t3_dissent_triggers_replan),
        ("status_distribution", t4_responses_have_status_distribution),
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
    print("✓ v2 改 2 验收 OK")
