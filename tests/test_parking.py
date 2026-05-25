"""[03] 停车实时车位测试。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.parking import (
    estimate_capacity, estimate_occupancy,
    estimate_parking, get_parking_score_adjust,
    ParkingEstimate,
)


class P:
    def __init__(self, name, lv2=""):
        self.name = name
        self.category_lv2 = lv2


# ============================================================
# 容量启发式
# ============================================================

def test_capacity_huge_mall():
    assert estimate_capacity(P("国贸商城", "商场")) >= 3000
    assert estimate_capacity(P("朝阳大悦城", "商场")) >= 3000


def test_capacity_park():
    assert estimate_capacity(P("玉渊潭公园", "公园广场")) >= 200


def test_capacity_hutong_no_parking():
    assert estimate_capacity(P("南锣鼓巷", "风景名胜")) == 0


def test_capacity_heritage_tight():
    """故宫小停车场。"""
    assert estimate_capacity(P("故宫博物院", "风景名胜")) <= 300


# ============================================================
# 占用率
# ============================================================

def test_occupancy_weekday_low():
    occ, _ = estimate_occupancy(3000, "国贸商城", datetime(2026, 5, 21, 14, 0))
    assert occ < 0.7


def test_occupancy_weekend_high():
    occ, _ = estimate_occupancy(200, "故宫博物院", datetime(2026, 5, 23, 14, 0))
    assert occ >= 0.85


def test_occupancy_holiday_extreme():
    occ, _ = estimate_occupancy(200, "故宫博物院", datetime(2026, 10, 4, 14, 0))
    assert occ >= 1.0


# ============================================================
# 综合估算
# ============================================================

def test_estimate_no_parking_for_hutong():
    e = estimate_parking(P("南锣鼓巷"))
    assert e.difficulty == "no_parking"
    assert e.wait_min >= 100


def test_estimate_easy_weekday():
    e = estimate_parking(P("国贸商城", "商场"), datetime(2026, 5, 21, 14, 0))
    assert e.difficulty in ("easy", "moderate")
    assert e.wait_min == 0


def test_estimate_extreme_holiday():
    e = estimate_parking(P("故宫博物院", "风景名胜"), datetime(2026, 10, 4, 14, 0))
    assert e.difficulty == "extreme"
    assert e.wait_min >= 10


def test_estimate_includes_fee():
    e = estimate_parking(P("国贸商城", "商场"))
    assert e.fee_per_hour > 0


# ============================================================
# Score 调整
# ============================================================

def test_adjust_no_parking_negative():
    delta, _ = get_parking_score_adjust(P("南锣鼓巷"))
    assert delta <= -0.15


def test_adjust_extreme_negative():
    delta, _ = get_parking_score_adjust(
        P("故宫博物院", "风景名胜"), datetime(2026, 10, 4, 14, 0))
    assert delta < 0


def test_adjust_easy_positive():
    delta, _ = get_parking_score_adjust(
        P("国贸商城", "商场"), datetime(2026, 5, 21, 14, 0))
    assert delta >= 0


# ============================================================
# rank_fuse 集成
# ============================================================

def test_fuse_with_driving():
    """driving=True 时，南锣鼓巷被减分（无停车场）。"""
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    pois = [
        POI(id="P1", name="国贸商城", category_lv1="购物服务",
            category_lv2="商场", category_lv3="商场",
            typecode="", district="", business_area="", address="",
            longitude=116.46, latitude=39.91, rating=4.5, avg_price=200,
            open_time="10:00-22:00", phone="", photos=[]),
        POI(id="P2", name="南锣鼓巷", category_lv1="风景名胜",
            category_lv2="风景名胜", category_lv3="街区",
            typecode="", district="", business_area="", address="",
            longitude=116.40, latitude=39.93, rating=4.5, avg_price=50,
            open_time="00:00-24:00", phone="", photos=[]),
    ]
    c = SearchConstraints(persona="family", min_rating=4.0)
    weekday = datetime(2026, 5, 21, 14, 0)

    ranked_walking = fuse_and_rank(pois, c, target_dt=weekday, driving=False)
    ranked_driving = fuse_and_rank(pois, c, target_dt=weekday, driving=True)

    s_nan_walk = next(r.score for r in ranked_walking if r.poi.id == "P2")
    s_nan_drive = next(r.score for r in ranked_driving if r.poi.id == "P2")
    assert s_nan_drive < s_nan_walk  # 开车去南锣被减分


def test_fuse_driving_off_no_reason():
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    p = POI(id="P1", name="南锣鼓巷", category_lv1="风景名胜",
            category_lv2="风景名胜", category_lv3="街区",
            typecode="", district="", business_area="", address="",
            longitude=116.40, latitude=39.93, rating=4.5, avg_price=50,
            open_time="00:00-24:00", phone="", photos=[])
    c = SearchConstraints(persona="family", min_rating=4.0)
    ranked = fuse_and_rank([p], c, driving=False)
    has_p = any(rs.factor.startswith("parking") for r in ranked for rs in r.reasons)
    assert has_p is False


# ============================================================
# probe 集成
# ============================================================

def test_probe_driving_blocks_hutong():
    """开车 + 南锣鼓巷 → probe 触发 reroute。"""
    from tools.availability_probe import probe
    from tools.types import POI

    poi = POI(
        id="P_NL", name="南锣鼓巷", category_lv1="风景名胜",
        category_lv2="风景名胜", category_lv3="街区",
        typecode="", district="", business_area="", address="",
        longitude=116.40, latitude=39.93, rating=4.5, avg_price=50,
        open_time="00:00-24:00", phone="", photos=[],
    )
    target = datetime(2026, 5, 23, 14, 0).strftime("%Y-%m-%dT%H:%M")
    result = probe(poi, target_time=target, party_size=3,
                   enable_weather=False, enable_closed=False, driving=True)
    assert "no_parking" in result.risk_tags or "parking_extreme" in result.risk_tags
    assert result.fallback_action == "reroute"


if __name__ == "__main__":
    import inspect
    fns = [f for n, f in globals().items() if n.startswith("test_") and inspect.isfunction(f)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"✓ {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"✗ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} 通过")
    sys.exit(failed)
