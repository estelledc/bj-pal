"""P0.4 验收：reroute 按改动幅度分流。

来源：USER_RESEARCH_FINDINGS 信号 9（小调整直接发，大调整先确认）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.planner import plan as make_plan  # noqa: E402
from agents.replanner import replan_step  # noqa: E402
from agents.types import RerouteEvent, UserPreferences  # noqa: E402
from tools.availability_probe import probe, user_dissent_probe  # noqa: E402
from tools.mock_message import render_reroute_notice  # noqa: E402
from tools.types import POI  # noqa: E402


def _build_plan_with_trap():
    """造一个会触发 trap reroute 的 plan。"""
    return make_plan(
        user_input="周六下午带 5 岁娃，4 小时左右",
        prefs=UserPreferences(persona="family", party_size=3,
                              has_child=True, child_age=5,
                              budget_per_person=120, target_start="14:00",
                              walk_radius_km=1.5),
    )


def t1_reroute_emits_summary():
    """所有 reroute 事件都应有 change_summary_zh 人话。"""
    p = _build_plan_with_trap()
    # 找方案中第一个非 depart step
    target_idx = next((i for i, s in enumerate(p.steps) if s.kind != "depart"), 0)
    poi_name = p.steps[target_idx].poi_name
    poi = POI(id="x", name=poi_name, category_lv1="风景名胜",
              category_lv2=None, category_lv3=None, typecode=None,
              district=None, business_area=None, address=None,
              longitude=None, latitude=None, rating=4.6, avg_price=0,
              open_time=None, phone=None, photos=[])
    pr = probe(poi, party_size=3, target_time="14:00", seed=42)
    if pr.fallback_action != "reroute":
        # 强制构造一个 reroute probe
        from tools.availability_probe import ProbeResult
        pr = ProbeResult(poi_id="x", poi_name=poi_name, status="unavailable",
                         wait_min=85, party_size=3, target_time="14:00",
                         evidence=["UGC[crowd]: 周末爆棚"], risk_tags=["queue"],
                         fallback_action="reroute", reason="queue")
    new_plan, ev = replan_step(p, target_idx, pr,
                                prefs=UserPreferences(persona="family"))
    print(f"\n[1] reroute event:")
    print(f"    magnitude:  {ev.change_magnitude}")
    print(f"    summary_zh: {ev.change_summary_zh}")
    print(f"    notify:     {ev.notify_strategy}")
    print(f"    unchanged:  {ev.unchanged_steps}")
    assert ev.change_summary_zh, "summary_zh 不应为空"
    assert ev.change_magnitude in ("small", "medium", "large", "none")
    assert isinstance(ev.unchanged_steps, list)
    assert len(ev.unchanged_steps) == len(p.steps) - 1
    return ev


def t2_user_dissent_triggers_reroute_with_summary():
    """user_dissent 类 reroute 也带 summary。"""
    p = _build_plan_with_trap()
    target_idx = next((i for i, s in enumerate(p.steps) if s.kind == "meal"), 1)
    poi_name = p.steps[target_idx].poi_name
    poi = POI(id="m", name=poi_name, category_lv1="餐饮服务",
              category_lv2="中餐厅", category_lv3=None, typecode=None,
              district=None, business_area=None, address=None,
              longitude=None, latitude=None, rating=4.5, avg_price=120,
              open_time=None, phone=None, photos=[])
    pr = user_dissent_probe(poi, party_size=3, target_time="15:00",
                            reason_text="老人吃不惯")
    new_plan, ev = replan_step(p, target_idx, pr,
                                prefs=UserPreferences(persona="family"))
    print(f"\n[2] user_dissent reroute:")
    print(f"    summary: {ev.change_summary_zh}")
    print(f"    reason:  {ev.reason}")
    assert "群里有人否决" in ev.change_summary_zh or "user_dissent" in ev.reason
    return ev


def t3_notice_card_routing_small_vs_medium():
    """notify_strategy=small → group_direct；private_first → 含 private_card。"""
    # 小幅 event
    ev_small = RerouteEvent(failed_step_idx=0, failed_poi_name="A",
                             reason="queue", replacement_poi_name="B",
                             change_magnitude="small",
                             change_summary_zh="原 14:00 A 改为 14:00 B（小调整）",
                             unchanged_steps=[1, 2, 3],
                             notify_strategy="group_direct")
    out_s = render_reroute_notice(ev_small)
    print(f"\n[3a] small → strategy={out_s['strategy']}")
    print(f"     group body: {out_s['group_card'].body}")
    assert out_s["strategy"] == "group_direct"
    assert out_s["group_card"] is not None
    assert out_s["private_card"] is None

    ev_mid = RerouteEvent(failed_step_idx=1, failed_poi_name="X",
                           reason="weather", replacement_poi_name="Y",
                           change_magnitude="medium",
                           change_summary_zh="原 15:00 X 改为 15:00 Y（换了片区）",
                           unchanged_steps=[0, 2, 3],
                           notify_strategy="private_first")
    out_m = render_reroute_notice(ev_mid)
    print(f"\n[3b] medium → strategy={out_m['strategy']}")
    print(f"     private body: {out_m['private_card'].body}")
    print(f"     send_order:   {out_m['send_order']}")
    assert out_m["strategy"] == "private_first"
    assert out_m["private_card"] is not None
    assert out_m["send_order"] == ["private", "group"]
    assert out_m["private_timeout_sec"] == 60
    return True


def t4_warn_only_when_no_replacement():
    """change_magnitude=none → warn_only。"""
    ev = RerouteEvent(failed_step_idx=0, failed_poi_name="孤立 POI",
                      reason="closed", replacement_poi_name=None,
                      change_magnitude="none",
                      change_summary_zh="找不到替补，第 1 站维持原计划但请留意风险",
                      unchanged_steps=[1, 2],
                      notify_strategy="warn_only")
    out = render_reroute_notice(ev)
    print(f"\n[4] warn_only out: {out['strategy']}")
    print(f"    body: {out['private_card'].body}")
    assert out["strategy"] == "warn_only"
    assert out["group_card"] is None
    assert "⚠" in out["private_card"].body
    return out


def t5_group_card_no_money():
    """notice 群卡也不能含 ¥。"""
    ev = RerouteEvent(failed_step_idx=0, failed_poi_name="A",
                       reason="queue", replacement_poi_name="B",
                       change_magnitude="small",
                       change_summary_zh="A 改为 B 人均 ¥220",
                       unchanged_steps=[1],
                       notify_strategy="group_direct")
    out = render_reroute_notice(ev)
    body = out["group_card"].body
    print(f"\n[5] group card body: {body}")
    assert "¥" not in body
    return body


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal P0.4 reroute 改动幅度分流 Tests")
    print("=" * 60)
    suite = [
        ("emits_summary", t1_reroute_emits_summary),
        ("user_dissent_summary", t2_user_dissent_triggers_reroute_with_summary),
        ("notice_routing", t3_notice_card_routing_small_vs_medium),
        ("warn_only", t4_warn_only_when_no_replacement),
        ("group_no_money", t5_group_card_no_money),
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
    print("✓ P0.4 验收 OK")
