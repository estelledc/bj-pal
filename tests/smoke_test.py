"""W1 D1 验收：query_pois('五道营', '餐饮') 返回 ≥1 条。

跑法：
    python3 tests/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from loader import query_pois, query_ugc, query_routes  # noqa: E402


def test_pois_keyword_search():
    """五道营片区附近的 POI（含餐饮 / 景点 / 商圈）。"""
    rows = query_pois(keyword="五道营")
    print(f"[1] query_pois(keyword='五道营') → {len(rows)} 条")
    for r in rows[:5]:
        print(f"    - {r['name']}  [{r['category_lv1']}/{r['category_lv2']}]  "
              f"rating={r['rating']}  {r['address']}")
    assert len(rows) >= 1, "五道营关键词应至少匹配 1 条 POI"
    return len(rows)


def test_pois_food_category():
    """真实高德餐饮 POI 应保持足够候选量。"""
    rows = query_pois(category="餐饮", limit=5000)
    print(f"\n[2] query_pois(category='餐饮') → {len(rows)} 条（取上限 5000）")
    for r in rows[:3]:
        print(f"    - {r['name']}  rating={r['rating']}  ¥{r['avg_price']}  {r['business_area']}")
    assert len(rows) >= 500, f"真实高德餐饮类目应有 ≥500 条，实际 {len(rows)}"
    return len(rows)


def test_pois_yonghegong_food():
    """五道营-雍和宫片区的高分餐饮——主 demo 候选。"""
    rows = query_pois(keyword="雍和宫", category="餐饮", min_rating=4.0)
    print(f"\n[3] query_pois('雍和宫', '餐饮', min_rating=4.0) → {len(rows)} 条")
    for r in rows:
        print(f"    - {r['name']}  rating={r['rating']}  ¥{r['avg_price']}")
    return len(rows)


def test_ugc_anchor():
    """UGC 应该按 area_anchor 能稳定查到五道营片区 11 条。"""
    rows = query_ugc(area_anchor="五道营")
    print(f"\n[4] query_ugc(area_anchor='五道营') → {len(rows)} 条")
    for r in rows[:5]:
        print(f"    - [{r['aspect_type']:15}] {r['poi_name']:20} "
              f"conf={r['confidence']:.2f}  {r['evidence_summary'][:40]}...")
    assert len(rows) >= 5, f"五道营片区 UGC 应有 ≥5 条，实际 {len(rows)}"
    return len(rows)


def test_ugc_risk_signals():
    """UGC 中带 risk_tags 的（reroute demo 触发器）。"""
    rows = query_ugc(aspect_types=["queue", "crowd", "booking_risk"])
    print(f"\n[5] query_ugc(aspect_types=[queue, crowd, booking_risk]) → {len(rows)} 条")
    for r in rows:
        print(f"    - {r['poi_name']:20} [{r['aspect_type']:12}] "
              f"sentiment={r['sentiment']:8} {r['evidence_summary'][:50]}...")
    assert len(rows) >= 1, "至少要有 1 条排队/拥堵风险信号"
    return len(rows)


def test_routes_modes():
    """52 条路线缓存按模式分布。"""
    rows = query_routes()
    print(f"\n[6] query_routes() → {len(rows)} 条")
    from collections import Counter
    modes = Counter(r["mode"] for r in rows)
    for mode, n in modes.most_common():
        print(f"    {mode:12} {n}")
    assert len(rows) >= 50, "应有 50+ 条路线缓存"
    return len(rows)


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal W1 D1 Smoke Test")
    print("=" * 60)
    results = {}
    failed = []
    for name, fn in [
        ("pois_yonghegong", test_pois_keyword_search),
        ("pois_food_total", test_pois_food_category),
        ("pois_yonghegong_food", test_pois_yonghegong_food),
        ("ugc_anchor", test_ugc_anchor),
        ("ugc_risks", test_ugc_risk_signals),
        ("routes", test_routes_modes),
    ]:
        try:
            results[name] = fn()
        except AssertionError as e:
            failed.append((name, str(e)))
            print(f"    ✗ {e}")
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"    ✗ 异常：{type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    for k, v in results.items():
        print(f"  {k:30} {v}")
    if failed:
        print(f"\n✗ {len(failed)} 项失败：")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print("\n✓ 全部通过 — W1 D1 验收 OK")
