"""W1 D5-D6 验收：availability_probe + Replanner reroute 链路。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.planner import plan as make_plan  # noqa: E402
from agents.replanner import probe_plan, replan_step  # noqa: E402
from agents.types import Plan, Step, UserPreferences  # noqa: E402
from tools.availability_probe import probe, list_trap_pois  # noqa: E402
from tools.types import POI  # noqa: E402


def t1_probe_trap_yonghegong():
    """雍和宫必须命中 trap，wait_min ≥ 30，fallback_action=reroute。"""
    poi = POI(
        id="poi-yh", name="雍和宫",
        category_lv1=None, category_lv2=None, category_lv3=None,
        typecode=None, district=None, business_area=None, address=None,
        longitude=None, latitude=None, rating=4.9, avg_price=None,
        open_time=None, phone=None, photos=[],
    )
    r = probe(poi, party_size=3, target_time="14:00", seed=42)
    print(f"\n[1] probe(雍和宫) → status={r.status} wait={r.wait_min}min "
          f"action={r.fallback_action}")
    print(f"    risk_tags={r.risk_tags}")
    print(f"    evidence: {r.evidence[0] if r.evidence else 'none'}")
    assert r.status in ("crowd_warn", "unavailable")
    assert r.wait_min >= 30
    assert r.fallback_action == "reroute"
    assert "holiday_crowd" in r.risk_tags or "entrance_queue" in r.risk_tags
    return r.wait_min


def t2_probe_normal_poi():
    """普通 POI 应该 ok 且 wait_min < 30。"""
    poi = POI(
        id="poi-fang", name="方砖厂69号炸酱面(雍和宫店)",
        category_lv1="餐饮服务", category_lv2=None, category_lv3=None,
        typecode=None, district=None, business_area=None, address=None,
        longitude=None, latitude=None, rating=4.7, avg_price=38.0,
        open_time=None, phone=None, photos=[],
    )
    r = probe(poi, party_size=3, target_time="14:00", seed=42)
    print(f"\n[2] probe(方砖厂炸酱面) → status={r.status} wait={r.wait_min}min "
          f"action={r.fallback_action}")
    assert r.fallback_action == "proceed"
    return r.wait_min


def t3_replan_step_replaces_yonghegong():
    """构造一个含雍和宫的 plan，reroute 后第 1 步换成同片区同类。"""
    plan = Plan(
        persona="family",
        area_anchor="五道营-雍和宫片区",
        steps=[
            Step(step_index=1, kind="culture", poi_id="poi-yh", poi_name="雍和宫",
                 start_time="14:00", duration_min=60, mode_to_here="walking",
                 rationale="北京代表性宗教文化点"),
            Step(step_index=2, kind="meal", poi_id="poi-jin", poi_name="金鼎轩(地坛店)",
                 start_time="15:00", duration_min=75, mode_to_here="walking",
                 rationale="家庭友好餐厅"),
        ],
        summary="test",
    )
    poi = POI(
        id="poi-yh", name="雍和宫",
        category_lv1=None, category_lv2=None, category_lv3=None,
        typecode=None, district=None, business_area=None, address=None,
        longitude=None, latitude=None, rating=4.9, avg_price=None,
        open_time=None, phone=None, photos=[],
    )
    probe_result = probe(poi, party_size=3, target_time="14:00", seed=42)
    new_plan, event = replan_step(plan, failed_step_idx=0, probe_result=probe_result,
                                  prefs=UserPreferences(persona="family"))
    print(f"\n[3] replan: 雍和宫 → {event.replacement_poi_name}")
    print(f"    rationale: {new_plan.steps[0].rationale[:100]}")
    print(f"    rerouted_at_step: {new_plan.rerouted_at_step}")
    assert event.replacement_poi_name is not None
    assert event.replacement_poi_name != "雍和宫"
    assert new_plan.steps[0].is_rerouted is True
    assert new_plan.rerouted_at_step == 0
    return event.replacement_poi_name


def t4_probe_plan_full_e2e():
    """端到端：生成 plan（mock）→ probe_plan 自动 reroute → 验证 events。"""
    p = make_plan(
        user_input="周六下午带 5 岁娃在雍和宫附近转转",
        prefs=UserPreferences(
            persona="family",
            has_child=True,
            child_age=5,
            budget_per_person=120,
            target_start="14:00",
        ),
    )
    print(f"\n[4] 原 plan：")
    for s in p.steps:
        print(f"    {s.step_index}. [{s.kind}] {s.poi_name}")
    new_plan, events = probe_plan(p, prefs=UserPreferences(persona="family"))
    print(f"\n    reroute 事件 {len(events)} 个：")
    for e in events:
        print(f"      step #{e.failed_step_idx}: {e.failed_poi_name} "
              f"→ {e.replacement_poi_name}")
        print(f"        原因：{e.reason}")
        print(f"        evidence：{e.evidence[0] if e.evidence else 'none'}")
    print(f"\n    最终 plan：")
    for s in new_plan.steps:
        marker = "⚠️" if s.is_rerouted else "  "
        print(f"    {marker} {s.step_index}. [{s.kind}] {s.poi_name}")
    return len(events)


def t5_trap_pois_have_data():
    """所有 trap POI 都应该是 demo 时能确定触发的——有 evidence 有 risk_tags。"""
    traps = list_trap_pois()
    print(f"\n[5] {len(traps)} 个 trap POI：{traps}")
    for name in traps:
        poi = POI(id=f"trap-{name}", name=name,
                  category_lv1=None, category_lv2=None, category_lv3=None,
                  typecode=None, district=None, business_area=None, address=None,
                  longitude=None, latitude=None, rating=None, avg_price=None,
                  open_time=None, phone=None, photos=[])
        r = probe(poi, target_time="14:00", seed=42)
        assert r.evidence, f"{name} 应有 evidence"
        assert r.fallback_action in ("warn", "reroute"), f"{name} 应触发 warn/reroute"
        print(f"    {name:20} wait={r.wait_min:3}min  action={r.fallback_action:8}  "
              f"tags={r.risk_tags[:2]}")
    return len(traps)


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal W1 D5-D6 Reroute Tests")
    print("=" * 60)
    suite = [
        ("probe_trap_yonghegong", t1_probe_trap_yonghegong),
        ("probe_normal_poi", t2_probe_normal_poi),
        ("replan_step_replaces", t3_replan_step_replaces_yonghegong),
        ("probe_plan_full_e2e", t4_probe_plan_full_e2e),
        ("trap_pois_have_data", t5_trap_pois_have_data),
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
    print("✓ W1 D5-D6 验收 OK")
