"""v2.6 D4 验收：time_bucket 时段扩展 — detect + 启发式打分 + ranking 集成。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.amap_search import resolve_area_center, search_pois  # noqa: E402
from tools.rank_fuse import fuse_and_rank  # noqa: E402
from tools.time_bucket import (  # noqa: E402
    detect_time_bucket,
    score_poi_for_bucket,
)
from tools.types import POI, SearchConstraints  # noqa: E402


def t1_detect_friday_night():
    cases = [
        "周五下班后跟同事喝点",
        "周五晚去簋街",
        "下班后找个店吃饭",
        "夜里找个酒馆",
    ]
    for q in cases:
        d = detect_time_bucket(q)
        assert d.bucket == "friday_night", f"{q} → {d.bucket}"
    print(f"\n[1] {len(cases)} 个 friday_night query 全部识别")


def t2_detect_rainy_indoor():
    cases = ["下雨天找个室内的地方", "下大雨想找个屋", "雷雨天去博物馆"]
    for q in cases:
        d = detect_time_bucket(q)
        assert d.bucket == "rainy_indoor", f"{q} → {d.bucket}"
    print(f"[2] {len(cases)} 个 rainy_indoor query 全部识别")


def t3_detect_holiday_morning():
    cases = ["春节大年初二带爹妈逛庙会", "假期早上家人出门", "上午去早茶"]
    for q in cases:
        d = detect_time_bucket(q)
        assert d.bucket == "holiday_morning", f"{q} → {d.bucket}"
    print(f"[3] {len(cases)} 个 holiday_morning query 全部识别")


def t4_detect_default_weekend_afternoon():
    cases = ["周六下午带娃溜达", "周末下午找个咖啡店", "周日下午"]
    for q in cases:
        d = detect_time_bucket(q)
        assert d.bucket == "weekend_afternoon", f"{q} → {d.bucket}"
    print(f"[4] {len(cases)} 个 weekend_afternoon query 全部识别")


def t5_detect_none_when_no_signal():
    """无时间词 → bucket=none，不触发画像调整。"""
    d = detect_time_bucket("4 人吃饭")
    assert d.bucket == "none"
    print(f"[5] 中性 query bucket=none confidence=0")


def t6_target_dt_inference():
    """target_dt 推断：周五 19h → friday_night。"""
    fri_night = datetime(2026, 6, 5, 19, 0)
    d = detect_time_bucket("出去吃个饭", target_dt=fri_night)
    assert d.bucket == "friday_night"
    print(f"[6] datetime 推断 OK：{d.evidence}")


def t7_score_poi_friday_night():
    """酒吧/烤肉在 friday_night 加分；博物馆减分。"""
    bar = POI(id="b", name="提督酒吧", category_lv1="休闲娱乐",
              category_lv2="酒吧", category_lv3=None, typecode=None,
              district=None, business_area=None, address=None,
              longitude=None, latitude=None, rating=4.5, avg_price=200,
              open_time=None, phone=None, photos=[])
    museum = POI(id="m", name="国家博物馆", category_lv1="科教文化",
                 category_lv2="博物馆", category_lv3=None, typecode=None,
                 district=None, business_area=None, address=None,
                 longitude=None, latitude=None, rating=4.7, avg_price=None,
                 open_time=None, phone=None, photos=[])
    d_bar, _ = score_poi_for_bucket(bar, "friday_night")
    d_mus, _ = score_poi_for_bucket(museum, "friday_night")
    assert d_bar > 0
    assert d_mus < 0
    print(f"[7] 酒吧/friday_night={d_bar:+.2f}  博物馆/friday_night={d_mus:+.2f}")


def t8_fuse_rank_with_time_context():
    """fuse_and_rank 接 time_context → 排序变化。"""
    c = SearchConstraints(persona="friends", min_rating=4.0,
                          walk_radius_km=2.0, budget_per_person=300)
    pois = search_pois(area_anchor="五道营-雍和宫片区", category="food",
                       constraints=c, limit=20)
    center = resolve_area_center("五道营-雍和宫片区")
    r_default = fuse_and_rank(pois, c, center=center)
    r_fn = fuse_and_rank(pois, c, center=center, time_context="friday_night")
    print(f"[8] default top1={r_default[0].poi.name}")
    print(f"     friday_night top1={r_fn[0].poi.name}")
    # 至少有 1 个 friday_night top5 命中烤/涮/火锅 keyword
    fn_top5_blob = " ".join(r.poi.name for r in r_fn[:5])
    assert any(kw in fn_top5_blob for kw in ["烤", "涮", "火锅", "酒"]), \
        f"friday_night top5 没烤肉/涮肉/火锅: {fn_top5_blob}"


def t9_planner_e2e_with_time_bucket():
    """plan() 端到端 — 周五晚 query 应让 plan 反映场景。"""
    from agents.planner import plan
    p_default = plan(user_input="4 人下午溜达吃饭", persona="friends")
    p_fn = plan(user_input="周五下班去簋街吃宵夜", persona="friends")
    print(f"[9] default plan: {[s.poi_name for s in p_default.steps[:4]]}")
    print(f"     friday_night plan: {[s.poi_name for s in p_fn.steps[:4]]}")
    # 二者 plan_id 不同
    assert p_default.plan_id != p_fn.plan_id


def t10_backward_compat_no_time_context():
    """time_context=None 时行为与旧版一致（无新 reason 增加）。"""
    c = SearchConstraints(persona="family", min_rating=4.0)
    pois = search_pois(area_anchor="五道营-雍和宫片区", category="food",
                       constraints=c, limit=10)
    r = fuse_and_rank(pois, c)
    for rp in r[:3]:
        for reason in rp.reasons:
            assert reason.factor not in ("time_bucket_match", "time_bucket_mismatch")
    print(f"[10] 无 time_context 时不出现 time_bucket reason，向后兼容")


if __name__ == "__main__":
    t1_detect_friday_night()
    t2_detect_rainy_indoor()
    t3_detect_holiday_morning()
    t4_detect_default_weekend_afternoon()
    t5_detect_none_when_no_signal()
    t6_target_dt_inference()
    t7_score_poi_friday_night()
    t8_fuse_rank_with_time_context()
    t9_planner_e2e_with_time_bucket()
    t10_backward_compat_no_time_context()
    print("\n所有 v2.6 D4 time_bucket 验收通过！")
