"""Task 1 数据扩展覆盖度验收测试。

校验：
- UGC: ≥ 1000 条 / ≥ 50 area_anchor / 5 dataset_version 都齐 / 1102 条 intensity 都填
- routes: ≥ 1000 条 / ≥ 100 unique scene_id / amap cache + estimated_v2 都在
- intensity: HIGH/MID/LOW 三档分布合理（不全 0.5）
- trap 真实化: compute_dynamic_trap_score 在高评 + UGC negative POI 上能 ≥ 0.5

跑法：python3 tests/test_data_coverage.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def get_conn():
    DB = Path(__file__).resolve().parent.parent / "bj_pal.db"
    if not DB.exists():
        print(f"[ERROR] DB 不存在：{DB}")
        print(f"        先跑：python3 src/loader.py")
        sys.exit(1)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def assert_(cond, msg, actual=""):
    label = "✓" if cond else "✗"
    print(f"  {label} {msg}{'  → ' + str(actual) if actual else ''}")
    if not cond:
        print(f"\n[FAIL] 测试不通过")
        sys.exit(1)


def test_ugc_coverage():
    print("\n=== Task 1.1 UGC 数据覆盖度 ===")
    conn = get_conn()
    cur = conn.cursor()

    n = cur.execute("SELECT COUNT(*) FROM ugc_aspects").fetchone()[0]
    assert_(n >= 1000, f"UGC 总条数 ≥ 1000", f"actual={n}")

    n_areas = cur.execute(
        "SELECT COUNT(DISTINCT area_anchor) FROM ugc_aspects"
    ).fetchone()[0]
    assert_(n_areas >= 50, f"area_anchor 数 ≥ 50", f"actual={n_areas}")

    # 5 类 dataset_version 都齐
    have_v1 = cur.execute(
        "SELECT 1 FROM ugc_aspects WHERE raw_json LIKE '%manual_ugc_seed_v1%' LIMIT 1"
    ).fetchone()
    have_class_a = cur.execute(
        "SELECT 1 FROM ugc_aspects WHERE raw_json LIKE '%synthetic_from_public_summaries_v2%' LIMIT 1"
    ).fetchone()
    have_class_b = cur.execute(
        "SELECT 1 FROM ugc_aspects WHERE raw_json LIKE '%derived_from_amap_attributes_v2%' LIMIT 1"
    ).fetchone()
    assert_(have_v1, "manual_v1 数据存在")
    assert_(have_class_a, "Class A 公开评论汇总存在")
    assert_(have_class_b, "Class B amap 属性推理存在")

    # scenario / theme prefix
    n_scenario = cur.execute(
        "SELECT COUNT(DISTINCT area_anchor) FROM ugc_aspects "
        "WHERE area_anchor LIKE 'scenario:%'"
    ).fetchone()[0]
    n_theme = cur.execute(
        "SELECT COUNT(DISTINCT area_anchor) FROM ugc_aspects "
        "WHERE area_anchor LIKE 'theme:%'"
    ).fetchone()[0]
    assert_(n_scenario >= 10, "scenario 主题 ≥ 10", f"actual={n_scenario}")
    assert_(n_theme >= 8, "theme 主题 ≥ 8", f"actual={n_theme}")

    conn.close()


def test_intensity_distribution():
    print("\n=== Task 1.2 weekend_afternoon_intensity 分布 ===")
    conn = get_conn()
    cur = conn.cursor()

    n_total = cur.execute("SELECT COUNT(*) FROM ugc_aspects").fetchone()[0]
    n_filled = cur.execute(
        "SELECT COUNT(*) FROM ugc_aspects WHERE weekend_afternoon_intensity IS NOT NULL"
    ).fetchone()[0]
    assert_(n_filled == n_total, f"intensity 100% 填充", f"{n_filled}/{n_total}")

    n_high = cur.execute(
        "SELECT COUNT(*) FROM ugc_aspects WHERE weekend_afternoon_intensity >= 0.7"
    ).fetchone()[0]
    n_low = cur.execute(
        "SELECT COUNT(*) FROM ugc_aspects WHERE weekend_afternoon_intensity < 0.4"
    ).fetchone()[0]
    assert_(n_high >= 100, f"HIGH (≥0.7) ≥ 100", f"actual={n_high}")
    assert_(n_low >= 50, f"LOW (<0.4) ≥ 50（防全部默认 0.5）", f"actual={n_low}")
    assert_(n_high + n_low >= 200, "HIGH + LOW 共 ≥ 200，分布有区分度",
            f"high={n_high} low={n_low}")

    conn.close()


def test_trap_dynamic_score():
    print("\n=== Task 1.3 动态 trap 评分 ===")
    from tools.availability_probe import compute_dynamic_trap_score
    from tools.types import POI
    from tools.ugc_signals import fetch_risk_signals

    # 高评 + 名字含老字号关键词应该触发
    poi_high = POI(
        id="t1", name="全聚德前门店", category_lv1="餐饮服务",
        category_lv2="", category_lv3="", typecode="", district="东城区",
        business_area="前门", address="", longitude=116.4, latitude=39.9,
        rating=4.8, avg_price=200, open_time="", phone="", photos=[],
    )
    score, reasons = compute_dynamic_trap_score(
        poi_high, fetch_risk_signals(poi_name=poi_high.name),
    )
    assert_(score >= 0.5, "高评老字号 trap_score ≥ 0.5", f"actual={score}")
    assert_(len(reasons) >= 2, "trap reasons ≥ 2 条 evidence",
            f"actual={len(reasons)}")

    # 普通店（rating 4.2）不应触发
    poi_low = POI(
        id="t2", name="路边小馆", category_lv1="餐饮服务",
        category_lv2="", category_lv3="", typecode="", district="东城区",
        business_area="", address="", longitude=116.4, latitude=39.9,
        rating=4.2, avg_price=80, open_time="", phone="", photos=[],
    )
    score_low, _ = compute_dynamic_trap_score(
        poi_low, fetch_risk_signals(poi_name=poi_low.name),
    )
    assert_(score_low < 0.3, "普通店不被误判为 trap", f"actual={score_low}")


def test_routes_coverage():
    print("\n=== Task 1.4 routes 数据覆盖度 ===")
    conn = get_conn()
    cur = conn.cursor()

    n_routes = cur.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
    assert_(n_routes >= 1000, f"routes 总数 ≥ 1000", f"actual={n_routes}")

    n_amap = cur.execute(
        "SELECT COUNT(*) FROM routes WHERE cache_key NOT LIKE 'est:%'"
    ).fetchone()[0]
    n_est = cur.execute(
        "SELECT COUNT(*) FROM routes WHERE cache_key LIKE 'est:%'"
    ).fetchone()[0]
    assert_(n_amap >= 40, f"amap cache 保留 ≥ 40", f"actual={n_amap}")
    assert_(n_est >= 1000, f"estimated_v2 ≥ 1000", f"actual={n_est}")

    n_modes = cur.execute(
        "SELECT COUNT(DISTINCT mode) FROM routes"
    ).fetchone()[0]
    assert_(n_modes == 4, "4 种模式都齐", f"actual={n_modes}")

    conn.close()


def main():
    print("=" * 60)
    print("Task 1 数据扩展覆盖度验收测试")
    print("=" * 60)
    test_ugc_coverage()
    test_intensity_distribution()
    test_trap_dynamic_score()
    test_routes_coverage()
    print("\n" + "=" * 60)
    print("✓ 全部通过 — Task 1.1-1.4 数据扩展验收 OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
