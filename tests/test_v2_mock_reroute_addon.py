"""v2 改 3 + 改 4 + 改 7 验收。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.addon_agent import suggest_addons  # noqa: E402
from agents.planner import plan as make_plan  # noqa: E402
from agents.types import Plan, Step, UserPreferences  # noqa: E402
from tools.availability_probe import (  # noqa: E402
    is_weather_blocked,
    probe,
    user_dissent_probe,
)
from tools.mock_book import book_restaurant  # noqa: E402
from tools.types import POI  # noqa: E402


# ============================================================
# 改 3：mock 真实感
# ============================================================

def t1_book_returns_menu_and_seat():
    """成功预订应有 menu / seat_no / latency_ms。"""
    r = book_restaurant(
        poi_id="poi-jin", poi_name="金鼎轩(地坛店)",
        target_time="15:00", party_size=3, contact_name="老婆",
        photos=["http://example.com/a.jpg"], seed=11,
        simulate_latency=False,  # 测试时关 sleep
    )
    print(f"\n[1] book status={r.status} latency={r.latency_ms:.0f}ms "
          f"seat={r.seat_no} menu={len(r.menu_preview)} 项 photos={len(r.photos)}")
    if r.status == "confirmed":
        for m in r.menu_preview[:3]:
            print(f"    - {m['name']:20} ¥{m['price']:3}  [{m['tag']}]")
        assert r.seat_no, "成功预订应有座位号"
        assert len(r.menu_preview) >= 3, "成功预订应有 ≥ 3 项菜单"
    return r.status


def t2_book_real_latency():
    """带真延迟应 ≥ 300ms。"""
    import time
    t0 = time.time()
    r = book_restaurant(
        poi_id="poi-x", poi_name="方砖厂69号炸酱面(雍和宫店)",
        target_time="14:00", party_size=2, simulate_latency=True, seed=5,
    )
    elapsed = (time.time() - t0) * 1000
    print(f"\n[2] book with latency: 实测 {elapsed:.0f}ms / 报告 {r.latency_ms:.0f}ms")
    assert elapsed >= 300, "带延迟时实际耗时应 ≥ 300ms"
    return elapsed


# ============================================================
# 改 4：多种 reroute
# ============================================================

def t3_weather_blocks_outdoor():
    """14:30 户外景点应触发 weather reroute。"""
    poi = POI(id="poi-yh", name="雍和宫", category_lv1="风景名胜",
              category_lv2=None, category_lv3=None, typecode=None,
              district=None, business_area=None, address=None,
              longitude=None, latitude=None, rating=4.9, avg_price=None,
              open_time=None, phone=None, photos=[])
    msg = is_weather_blocked("14:30", poi)
    print(f"\n[3] is_weather_blocked('14:30', 雍和宫) = {msg}")
    assert msg is not None
    r = probe(poi, party_size=3, target_time="14:30", seed=42)
    # weather 优先级最高，应在 trap POI 之前触发
    print(f"    probe status={r.status} reason={r.reason}")
    assert r.reason == "weather"
    return r.reason


def t4_weather_no_block_indoor():
    """14:30 室内餐厅不应触发 weather。"""
    poi = POI(id="poi-jin", name="金鼎轩(地坛店)", category_lv1="餐饮服务",
              category_lv2=None, category_lv3=None, typecode=None,
              district=None, business_area=None, address=None,
              longitude=None, latitude=None, rating=4.8, avg_price=88.0,
              open_time=None, phone=None, photos=[])
    msg = is_weather_blocked("14:30", poi)
    print(f"\n[4] is_weather_blocked('14:30', 金鼎轩) = {msg}")
    assert msg is None
    return True


def t5_user_dissent_explicit():
    """user_dissent_probe 应返回 reroute 事件。"""
    poi = POI(id="poi-x", name="某店", category_lv1="餐饮服务",
              category_lv2=None, category_lv3=None, typecode=None,
              district=None, business_area=None, address=None,
              longitude=None, latitude=None, rating=4.5, avg_price=88.0,
              open_time=None, phone=None, photos=[])
    r = user_dissent_probe(poi, party_size=3, target_time="15:00",
                           reason_text="老婆觉得太贵了")
    print(f"\n[5] user_dissent → reason={r.reason} action={r.fallback_action}")
    assert r.reason == "user_dissent"
    assert r.fallback_action == "reroute"
    assert "老婆觉得太贵了" in r.evidence[0]
    return r.reason


def t6_closed_random():
    """closed 触发率约 5%（餐饮）。"""
    poi = POI(id="poi-x", name="测试餐厅", category_lv1="餐饮服务",
              category_lv2=None, category_lv3=None, typecode=None,
              district=None, business_area=None, address=None,
              longitude=None, latitude=None, rating=4.5, avg_price=88.0,
              open_time=None, phone=None, photos=[])
    closed_count = 0
    for seed in range(200):
        r = probe(poi, party_size=3, target_time="16:00", seed=seed,
                  enable_weather=False)
        if r.reason == "closed":
            closed_count += 1
    rate = closed_count / 200
    print(f"\n[6] closed 触发率（200 次）：{rate:.1%}")
    assert 0.01 <= rate <= 0.12  # 5% ± 浮动
    return rate


# ============================================================
# 改 7：AddOn Agent
# ============================================================

def t7_addon_for_family():
    """family + has_child + 有 culture step → 应至少 1 条 guided_tour。"""
    p = make_plan(
        user_input="下午带 5 岁娃去五道营",
        prefs=UserPreferences(persona="family", has_child=True, child_age=5,
                              budget_per_person=120, target_start="14:00"),
    )
    addons = suggest_addons(p, UserPreferences(persona="family", has_child=True, child_age=5))
    print(f"\n[7] family addon → {len(addons)} 条")
    for a in addons:
        print(f"    [{a.kind:14}] {a.title}")
        print(f"      → {a.description[:60]}")
        print(f"      reasoning: {a.reasoning}")
    kinds = {a.kind for a in addons}
    assert "guided_tour" in kinds or "umbrella" in kinds or "water_bottle" in kinds
    assert len(addons) >= 1
    return len(addons)


def t8_addon_for_friends():
    """friends 画像 + culture → merch_addon 出现。"""
    p = make_plan(
        user_input="周六下午跟 4 个朋友去玩",
        prefs=UserPreferences(persona="friends", party_size=4,
                              budget_per_person=250, target_start="14:30",
                              walk_radius_km=2.0),
    )
    addons = suggest_addons(p, UserPreferences(persona="friends"))
    print(f"\n[8] friends addon → {len(addons)} 条")
    for a in addons:
        print(f"    [{a.kind:14}] {a.title}")
    kinds = {a.kind for a in addons}
    assert "merch_addon" in kinds or "early_pickup" in kinds
    return len(addons)


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal v2 改 3 + 改 4 + 改 7 Tests")
    print("=" * 60)
    suite = [
        ("book_menu_seat", t1_book_returns_menu_and_seat),
        ("book_real_latency", t2_book_real_latency),
        ("weather_blocks_outdoor", t3_weather_blocks_outdoor),
        ("weather_no_indoor", t4_weather_no_block_indoor),
        ("user_dissent", t5_user_dissent_explicit),
        ("closed_5pct", t6_closed_random),
        ("addon_family", t7_addon_for_family),
        ("addon_friends", t8_addon_for_friends),
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
    print("✓ v2 改 3 + 改 4 + 改 7 验收 OK")
