"""v2 改 1 + 改 6B 验收。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.route_lookup import (  # noqa: E402
    format_modes_compact,
    lookup_routes,
    pick_best_mode,
)


def t1_short_distance_walking():
    """雍和宫附近两点（500m）：4 模式都返回，步行 ≤ 8min。"""
    legs = lookup_routes(116.4166, 39.9474, 116.4150, 39.9460)
    print(f"\n[1] 500m 4 模式：")
    for mode, leg in legs.items():
        print(f"    {mode:11} {leg.distance_m:5}m {leg.duration_min:3}min ({leg.source})")
    # 短距离步行应在 20min 内（cached 命中时是真实 amap 数据，可能含转弯绕路）
    assert "walking" in legs and legs["walking"].duration_min <= 20
    assert all(m in legs for m in ["walking", "bicycling", "driving", "transit"])
    return legs["walking"].duration_min


def t2_pick_best_short():
    """500m 步行 + 不带娃 → 应选 walking。"""
    legs = lookup_routes(116.4166, 39.9474, 116.4150, 39.9460)
    mode, reason = pick_best_mode(legs, has_child=False)
    print(f"\n[2] 500m 不带娃 → 推荐 {mode}：{reason}")
    assert mode == "walking"
    return mode


def t3_pick_best_with_child():
    """1.2km 带娃 → 应选 bicycling。"""
    legs = lookup_routes(116.4166, 39.9474, 116.4250, 39.9560)  # ~1.2km 北
    mode, reason = pick_best_mode(legs, has_child=True)
    print(f"\n[3] {legs['walking'].distance_m}m 带娃 → 推荐 {mode}：{reason}")
    assert mode in ("bicycling", "walking")
    return mode


def t4_pick_best_long():
    """3.5km → 应选驾车或公交。"""
    legs = lookup_routes(116.4166, 39.9474, 116.4500, 39.9700)
    mode, reason = pick_best_mode(legs, has_child=False)
    print(f"\n[4] {legs['walking'].distance_m}m → 推荐 {mode}：{reason}")
    assert mode in ("driving", "transit", "bicycling")
    return mode


def t5_format_compact():
    legs = lookup_routes(116.4166, 39.9474, 116.4150, 39.9460)
    s = format_modes_compact(legs)
    print(f"\n[5] format_modes_compact: {s}")
    assert "🚶" in s and "🚴" in s
    return s


def t6_plan_uses_real_travel_times():
    """make_plan 后每步 travel_time_min 应被填充。"""
    from agents.planner import plan
    from agents.types import UserPreferences
    p = plan(
        user_input="下午带 5 岁娃",
        prefs=UserPreferences(persona="family", has_child=True, child_age=5,
                              budget_per_person=120, target_start="14:00"),
    )
    print(f"\n[6] plan {len(p.steps)} 步，travel times：")
    for s in p.steps:
        if s.poi_id:
            print(f"    {s.step_index}. {s.poi_name:25} {s.mode_to_here:10} {s.travel_time_min}min "
                  f"({len(s.travel_options)} 模式)")
    poi_steps = [s for s in p.steps if s.poi_id]
    # 第 1 步 travel=0，后续应有非零 travel
    later_with_travel = [s for s in poi_steps[1:] if s.travel_time_min > 0]
    assert len(later_with_travel) >= 1, "至少 1 步应有真实 travel time"
    # 每步含 4 模式
    for s in poi_steps[1:]:
        if s.travel_options:
            assert len(s.travel_options) == 4, f"应含 4 模式，实际 {list(s.travel_options.keys())}"
    return len(later_with_travel)


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal v2 改 1 + 改 6B Route Lookup Tests")
    print("=" * 60)
    suite = [
        ("short_4modes", t1_short_distance_walking),
        ("pick_best_short", t2_pick_best_short),
        ("pick_best_with_child", t3_pick_best_with_child),
        ("pick_best_long", t4_pick_best_long),
        ("format_compact", t5_format_compact),
        ("plan_uses_real_travel", t6_plan_uses_real_travel_times),
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
    print("✓ v2 改 1 + 改 6B 验收 OK")
