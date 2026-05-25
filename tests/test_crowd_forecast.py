"""[42] 节假日人流预测测试。"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.crowd_forecast import (
    get_holiday, is_weekend, is_famous_outdoor_poi,
    crowd_multiplier, get_crowd_score_adjust, _load,
)


# ============================================================
# 日历
# ============================================================

def test_load_holidays():
    cal = _load()
    assert len(cal["holidays"]) >= 6
    # 国庆 tier_1
    names = [h["name"] for h in cal["holidays"]]
    assert "国庆节" in names
    assert "春节" in names


def test_get_holiday_within_range():
    """5/1 在劳动节假期内。"""
    h = get_holiday(date(2026, 5, 3))
    assert h is not None
    assert h.name == "劳动节"
    assert h.tier == "tier_1_extreme"


def test_get_holiday_no_match():
    h = get_holiday(date(2026, 5, 21))
    assert h is None


def test_is_weekend():
    assert is_weekend(date(2026, 5, 23)) is True   # 周六
    assert is_weekend(date(2026, 5, 24)) is True   # 周日
    assert is_weekend(date(2026, 5, 21)) is False  # 周四


def test_is_famous_outdoor_poi():
    assert is_famous_outdoor_poi("故宫博物院") is True
    assert is_famous_outdoor_poi("玉渊潭公园") is True
    assert is_famous_outdoor_poi("某神秘咖啡馆") is False


# ============================================================
# 倍率
# ============================================================

def test_multiplier_weekday_modest():
    """周四下午故宫，平日 hour 1.5；不叠加 famous_boost (因非节假日 / 非周末)。"""
    m, _ = crowd_multiplier("故宫博物院", datetime(2026, 5, 21, 14, 0))
    assert 1.0 < m < 2.0


def test_multiplier_weekend_high():
    """周六下午故宫：weekend 1.5 × hour 1.5 × famous 1.4 ≈ 3.15。"""
    m, _ = crowd_multiplier("故宫博物院", datetime(2026, 5, 23, 14, 0))
    assert m > 2.5


def test_multiplier_holiday_extreme():
    """国庆 + 故宫 + 14:00：tier_1 2.5 × hour 1.5 × famous 1.4 ≈ 5.25。"""
    m, why = crowd_multiplier("故宫博物院", datetime(2026, 10, 4, 14, 0))
    assert m > 4.0
    assert "国庆" in why


def test_multiplier_unknown_poi_holiday():
    """非热门 POI 国庆只受 tier 影响，没有 famous_boost。"""
    m, _ = crowd_multiplier("某无名小店", datetime(2026, 10, 4, 14, 0))
    assert 2.0 <= m <= 4.5


def test_score_adjust_thresholds():
    """阈值边界测试。"""
    # 平日 1.5 → -0.05
    delta, _ = get_crowd_score_adjust("故宫博物院", datetime(2026, 5, 21, 14, 0))
    assert delta < 0
    # 国庆 5.25 → -0.30
    delta_h, _ = get_crowd_score_adjust("故宫博物院", datetime(2026, 10, 4, 14, 0))
    assert delta_h <= -0.20


# ============================================================
# rank_fuse 集成
# ============================================================

def test_fuse_with_target_dt_holiday():
    """目标日是国庆 → 故宫被降权。"""
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    p = POI(id="P_GUGONG", name="故宫博物院", category_lv1="风景名胜",
            category_lv2="风景名胜", category_lv3="博物馆",
            typecode="", district="", business_area="", address="",
            longitude=116.397, latitude=39.916, rating=4.7, avg_price=60,
            open_time="08:30-17:00", phone="", photos=[])
    c = SearchConstraints(persona="family", min_rating=4.0)
    holiday_dt = datetime(2026, 10, 4, 14, 0)
    weekday_dt = datetime(2026, 5, 21, 14, 0)

    ranked_h = fuse_and_rank([p], c, target_dt=holiday_dt, crowd_aware=True)
    ranked_w = fuse_and_rank([p], c, target_dt=weekday_dt, crowd_aware=True)

    s_h = ranked_h[0].score
    s_w = ranked_w[0].score
    assert s_h < s_w  # 国庆比周四分低


def test_fuse_crowd_off():
    """crowd_aware=False 时不加 reason。"""
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    p = POI(id="P1", name="故宫博物院", category_lv1="风景名胜",
            category_lv2="风景名胜", category_lv3="博物馆",
            typecode="", district="", business_area="", address="",
            longitude=116.397, latitude=39.916, rating=4.7, avg_price=60,
            open_time="08:30-17:00", phone="", photos=[])
    c = SearchConstraints(persona="family", min_rating=4.0)
    ranked = fuse_and_rank([p], c, target_dt=datetime(2026, 10, 4), crowd_aware=False)
    has_crowd = any(rs.factor == "holiday_crowd_penalty"
                    for r in ranked for rs in r.reasons)
    assert has_crowd is False


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
