"""W1 D2 验收：amap_search + ugc_signals 两个 tool 模块。

跑法：
    python3 tests/test_tools.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.amap_search import (  # noqa: E402
    list_known_areas,
    resolve_area_center,
    search_pois,
)
from tools.types import SearchConstraints  # noqa: E402
from tools.ugc_signals import (  # noqa: E402
    fetch_aspects,
    fetch_risk_signals,
    fetch_scenario_fit,
    soft_score_for_poi,
    summarize_area,
)


# ============================================================
# amap_search
# ============================================================

def t1_search_yonghegong_food():
    """五道营片区餐饮，半径 1.5km，评分 ≥ 4.5。"""
    pois = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="food",
        constraints=SearchConstraints(
            persona="family",
            min_rating=4.5,
            walk_radius_km=1.5,
        ),
        limit=10,
    )
    print(f"[1] 五道营-雍和宫 餐饮 ≥4.5  → {len(pois)} 条")
    for p in pois[:5]:
        print(f"    - {p.name}  {p.rating}  ¥{p.avg_price}  {p.business_area}")
    assert len(pois) >= 5, f"应至少 5 条，实际 {len(pois)}"
    # 应都是餐饮服务
    assert all(p.category_lv1 == "餐饮服务" for p in pois), "类目应全是餐饮服务"
    return len(pois)


def t2_search_with_budget():
    """家庭画像：预算 ≤ 100/人，应能筛掉 ¥966 的京兆尹。"""
    pois = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="food",
        constraints=SearchConstraints(
            min_rating=4.5,
            budget_per_person=100,
            walk_radius_km=1.5,
        ),
        limit=20,
    )
    print(f"\n[2] 五道营-雍和宫 餐饮 预算≤100 → {len(pois)} 条")
    for p in pois[:5]:
        print(f"    - {p.name}  ¥{p.avg_price}")
    # 京兆尹 ¥966 不应出现
    names = [p.name for p in pois]
    assert "京兆尹(雍和宫店)" not in names, "京兆尹 ¥966 应被预算过滤掉"
    return len(pois)


def t3_search_child_friendly():
    """带 5 岁娃，应排除酒吧 / 夜店。"""
    pois = search_pois(
        area_anchor="王府井-东单片区",
        category="all",
        constraints=SearchConstraints(
            persona="family",
            has_child=True,
            child_age=5,
            walk_radius_km=2.0,
            min_rating=4.0,
        ),
        limit=30,
    )
    print(f"\n[3] 王府井 全类目 带 5 岁娃 → {len(pois)} 条")
    blacklist = ["酒吧", "夜店", "清吧", "ktv", "电竞"]
    for p in pois:
        blob = f"{p.name} {p.category_lv2} {p.category_lv3}".lower()
        assert not any(kw in blob for kw in blacklist), f"亲子场景不应包含 {p.name}"
    return len(pois)


def t4_search_open_at():
    """营业时间过滤：14:00 应该过滤掉部分晚餐店。"""
    pois_no_time = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="food",
        constraints=SearchConstraints(min_rating=4.5, walk_radius_km=1.5),
        limit=50,
    )
    pois_at_14 = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="food",
        constraints=SearchConstraints(
            min_rating=4.5,
            walk_radius_km=1.5,
            open_at="2026-05-18T14:00",
        ),
        limit=50,
    )
    print(f"\n[4] 营业时间过滤 14:00 → 无过滤 {len(pois_no_time)} → 有过滤 {len(pois_at_14)}")
    assert len(pois_at_14) <= len(pois_no_time), "营业时间过滤后应 ≤ 原集"
    return len(pois_at_14)


def t5_known_areas():
    """7 个已知片区都能解析中心点。"""
    areas = list_known_areas()
    print(f"\n[5] 已知片区 {len(areas)} 个")
    for a in areas:
        center = resolve_area_center(a)
        assert center is not None, f"{a} 应能解析"
        print(f"    {a:25} center=({center[0]:.4f}, {center[1]:.4f})")
    return len(areas)


# ============================================================
# ugc_signals
# ============================================================

def t6_risk_signals_yonghegong():
    """五道营片区拉风险信号，应至少含 queue/crowd/transport。"""
    risks = fetch_risk_signals(area_anchor="五道营-雍和宫片区")
    print(f"\n[6] 五道营-雍和宫 风险信号 → {len(risks)} 条")
    types = {r.aspect_type for r in risks}
    print(f"    aspect_types: {types}")
    for r in risks:
        tags = r.risk_tags()
        print(f"    - [{r.aspect_type:10}] {r.poi_name:15} sentiment={r.sentiment:8} risk_tags={tags}")
    assert "queue" in types or "crowd" in types or "transport" in types, \
        "至少要有 queue/crowd/transport 之一"
    return len(risks)


def t7_scenario_fit():
    """五道营片区 scenario_fit 聚合。"""
    fit = fetch_scenario_fit("五道营-雍和宫片区")
    print(f"\n[7] 五道营-雍和宫 scenario_fit → {len(fit)} 个场景标签")
    for scene, score in sorted(fit.items(), key=lambda x: -x[1])[:8]:
        print(f"    {scene:20} {score:.2f}")
    assert len(fit) >= 1, "应有至少 1 个场景标签"
    return len(fit)


def t8_soft_score():
    """雍和宫的 UGC 综合软分（含负面排队信号）。"""
    score, aspects = soft_score_for_poi("雍和宫")
    print(f"\n[8] 雍和宫 UGC soft score = {score:.3f}（{len(aspects)} 条 aspects）")
    for a in aspects[:5]:
        print(f"    - [{a.aspect_type:12}] {a.sentiment:8} conf={a.confidence:.2f}  "
              f"{a.evidence_summary[:40]}...")
    assert len(aspects) >= 1, "雍和宫应有 UGC 数据"
    return score


def t9_summarize_area():
    """summarize_area('五道营-雍和宫片区') 完整结构。"""
    s = summarize_area("五道营-雍和宫片区")
    print(f"\n[9] summarize_area('五道营-雍和宫片区')")
    print(f"    aspect_counts: {s['aspect_counts']}")
    print(f"    risk_tags_top: {s['risk_tags_top']}")
    print(f"    scene_tags_top: {s['scene_tags_top']}")
    print(f"    scenario_fit: {dict(list(s['scenario_fit'].items())[:5])}")
    print(f"    mentioned_pois: {s['mentioned_pois']}")
    assert s["aspect_counts"], "aspect_counts 应非空"
    assert len(s["mentioned_pois"]) >= 2, "应有 ≥ 2 个 POI"
    return len(s["mentioned_pois"])


def t10_reroute_trigger_candidates():
    """reroute 触发器 demo：找一个可作陷阱的 POI（高风险 + 高知名度）。"""
    risks = fetch_risk_signals()
    high_risk_pois = [
        r for r in risks
        if r.sentiment == "negative" and r.confidence >= 0.8
    ]
    print(f"\n[10] reroute 候选触发 POI（negative + conf≥0.8）→ {len(high_risk_pois)} 条")
    for r in high_risk_pois:
        print(f"    - {r.poi_name:15} [{r.aspect_type:12}] conf={r.confidence:.2f}  "
              f"risk_tags={r.risk_tags()}  → {r.evidence_summary[:40]}...")
    assert len(high_risk_pois) >= 2, "至少 2 个高置信度负面信号才能编排 demo"
    return len(high_risk_pois)


# ============================================================
# main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal W1 D2 Tool Tests")
    print("=" * 60)
    suite = [
        ("search_yonghegong_food", t1_search_yonghegong_food),
        ("search_with_budget", t2_search_with_budget),
        ("search_child_friendly", t3_search_child_friendly),
        ("search_open_at_14", t4_search_open_at),
        ("known_areas", t5_known_areas),
        ("risk_signals_yh", t6_risk_signals_yonghegong),
        ("scenario_fit", t7_scenario_fit),
        ("soft_score_yonghegong", t8_soft_score),
        ("summarize_area", t9_summarize_area),
        ("reroute_candidates", t10_reroute_trigger_candidates),
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
            print(f"    ✗ 异常：{type(e).__name__}: {e}")
    print("\n" + "=" * 60)
    if failed:
        print(f"✗ {len(failed)} 项失败：")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print("✓ 全部通过 — W1 D2 验收 OK")
