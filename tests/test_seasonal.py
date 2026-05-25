"""[05] 季节限定 / 节庆 / 网红期窗口测试。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.seasonal import (
    SEASON_KEYWORDS, MONTH_TO_SEASON,
    SeasonalSignal,
    build_index, get_signal, get_season_match,
    current_season, get_top_seasonal_pois,
)


# ============================================================
# 关键词库 + 月份映射
# ============================================================

def test_month_to_season():
    assert MONTH_TO_SEASON[3] == "spring"
    assert MONTH_TO_SEASON[5] == "spring"
    assert MONTH_TO_SEASON[7] == "summer"
    assert MONTH_TO_SEASON[10] == "autumn"
    assert MONTH_TO_SEASON[1] == "winter"
    assert MONTH_TO_SEASON[12] == "winter"


def test_keywords_complete():
    assert "樱花" in SEASON_KEYWORDS["spring"]
    assert "银杏" in SEASON_KEYWORDS["autumn"]
    assert "雪" in SEASON_KEYWORDS["winter"]
    assert "庙会" in SEASON_KEYWORDS["festival"]


# ============================================================
# 索引构建
# ============================================================

def test_build_index_size():
    n = build_index()
    assert n > 1000  # 至少 1000+ POI 进过索引


def test_signal_dataclass_methods():
    sig = SeasonalSignal(poi_name="X", spring_pos=3, autumn_pos=4, summer_neg=3)
    assert "spring" in sig.peak_seasons(threshold=2)
    assert "autumn" in sig.peak_seasons(threshold=2)
    assert "summer" in sig.avoid_seasons(threshold=2)
    assert sig.is_seasonal_poi() is True


# ============================================================
# 真实 POI 信号
# ============================================================

def test_yuyuantan_spring_and_autumn_peak():
    """玉渊潭 4 月（樱花季）+ 11 月（银杏季）应触发 peak。"""
    apr = get_season_match("玉渊潭公园", today=date(2026, 4, 15))
    nov = get_season_match("玉渊潭公园", today=date(2026, 11, 15))
    jul = get_season_match("玉渊潭公园", today=date(2026, 7, 15))

    assert apr["is_peak"] is True
    assert apr["score_adjust"] > 0
    assert nov["is_peak"] is True
    assert jul["is_peak"] is False  # 夏天不是樱/银杏季


def test_olympic_forest_summer_avoid():
    """奥森夏天暴晒 UGC 多 → summer 应识别为 avoid。"""
    aug = get_season_match("奥林匹克森林公园", today=date(2026, 8, 15))
    # 应该至少 score_adjust ≤ 0（不会 boost）
    assert aug["score_adjust"] <= 0


def test_unknown_poi_no_signal():
    out = get_season_match("某神秘小店", today=date(2026, 5, 21))
    assert out["score_adjust"] == 0.0
    assert out["reason"] == ""


def test_current_season_today():
    s = current_season(today=date(2026, 5, 21))
    assert s == "spring"


def test_top_seasonal_pois():
    spring_top = get_top_seasonal_pois("spring", top_k=5)
    assert len(spring_top) >= 1
    # 计数单调递减
    for i in range(1, len(spring_top)):
        assert spring_top[i][1] <= spring_top[i-1][1]


# ============================================================
# rank_fuse 集成
# ============================================================

def test_fuse_and_rank_seasonal_aware():
    """seasonal_aware=True 时玉渊潭 4 月相对于 7 月分数差异（同 POI 不同当前月）。"""
    # 我们没法控制 today，只能验证存在 reason
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    p = POI(id="P_YUY", name="玉渊潭公园", category_lv1="风景名胜",
            category_lv2="公园", category_lv3="公园",
            typecode="", district="", business_area="", address="",
            longitude=116.305, latitude=39.917,
            rating=4.6, avg_price=2, open_time="06:00-21:00",
            phone="", photos=[])
    constraints = SearchConstraints(persona="family", min_rating=4.0)
    ranked = fuse_and_rank([p], constraints, seasonal_aware=True)
    # 当前月（5 月）= spring，玉渊潭 spring 是 peak，应有 seasonal_peak reason
    has_seasonal = any(rs.factor in ("seasonal_peak", "seasonal_avoid")
                       for r in ranked for rs in r.reasons)
    assert has_seasonal


def test_fuse_and_rank_seasonal_off_default():
    """seasonal_aware=False 不加任何 seasonal reason。"""
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    p = POI(id="P_YUY", name="玉渊潭公园", category_lv1="风景名胜",
            category_lv2="公园", category_lv3="公园",
            typecode="", district="", business_area="", address="",
            longitude=116.305, latitude=39.917,
            rating=4.6, avg_price=2, open_time="06:00-21:00",
            phone="", photos=[])
    constraints = SearchConstraints(persona="family", min_rating=4.0)
    ranked = fuse_and_rank([p], constraints, seasonal_aware=False)
    has_seasonal = any(rs.factor.startswith("seasonal")
                       for r in ranked for rs in r.reasons)
    assert has_seasonal is False


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
