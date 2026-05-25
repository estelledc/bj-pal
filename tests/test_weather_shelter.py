"""[16] 天气降级路线测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.weather_shelter import (
    classify_poi,
    infer_weather_from_month,
    get_weather_adjust,
    WeatherContext,
)


class P:
    def __init__(self, name, lv1="", lv2="", lv3=""):
        self.name = name
        self.category_lv1 = lv1
        self.category_lv2 = lv2
        self.category_lv3 = lv3


# ============================================================
# 启发式分类
# ============================================================

def test_classify_indoor_by_name():
    assert classify_poi(P("某博物馆", "风景名胜", "风景名胜")) == "full_indoor"
    assert classify_poi(P("某书店")) == "full_indoor"


def test_classify_open_by_name():
    assert classify_poi(P("玉渊潭公园", "风景名胜", "公园广场")) == "open"
    assert classify_poi(P("南锣鼓巷")) == "open"


def test_classify_subway_direct():
    assert classify_poi(P("国贸商城", "购物服务", "商场")) == "subway_direct"
    assert classify_poi(P("万象城")) == "subway_direct"


def test_classify_restaurant_indoor():
    assert classify_poi(P("胡大饭馆", "餐饮服务", "中餐厅")) == "full_indoor"


def test_classify_unknown_defaults_open():
    assert classify_poi(P("未知 POI")) == "open"


# ============================================================
# 月份推断
# ============================================================

def test_winter_cold():
    w = infer_weather_from_month(1)
    assert w.state == "cold"


def test_summer_heatwave():
    assert infer_weather_from_month(7).state == "heatwave"
    assert infer_weather_from_month(8).state == "heatwave"


def test_clear_in_october():
    assert infer_weather_from_month(10).state == "clear"


# ============================================================
# 加分 / 减分
# ============================================================

def test_rain_indoor_boost():
    rain = WeatherContext(state="rain", description="下雨", severity=0.7)
    indoor = P("故宫博物院", "风景名胜", "风景名胜", "博物馆")
    delta, why = get_weather_adjust(indoor, rain)
    assert delta > 0
    assert "好选择" in why


def test_rain_outdoor_penalty():
    rain = WeatherContext(state="rain", description="下雨", severity=0.7)
    park = P("玉渊潭公园", "风景名胜", "公园广场")
    delta, why = get_weather_adjust(park, rain)
    assert delta < 0
    assert "体验差" in why


def test_clear_no_adjust():
    clear = WeatherContext(state="clear")
    park = P("玉渊潭公园", "风景名胜", "公园广场")
    delta, why = get_weather_adjust(park, clear)
    assert delta == 0.0
    assert why == ""


def test_aqi_high_outdoor_severe_penalty():
    """雾霾红色比下雨更严厉。"""
    aqi = WeatherContext(state="aqi_high", description="雾霾", severity=0.9)
    park = P("玉渊潭公园", "风景名胜", "公园广场")
    delta, _ = get_weather_adjust(park, aqi)
    assert delta <= -0.20


# ============================================================
# rank_fuse 集成
# ============================================================

def test_fuse_and_rank_with_weather():
    """雨天玉渊潭公园会被降权。"""
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    pois = [
        POI(id="P1", name="国贸商城", category_lv1="购物服务",
            category_lv2="商场", category_lv3="商场",
            typecode="", district="", business_area="", address="",
            longitude=116.46, latitude=39.91, rating=4.5, avg_price=200,
            open_time="10:00-22:00", phone="", photos=[]),
        POI(id="P2", name="玉渊潭公园", category_lv1="风景名胜",
            category_lv2="公园广场", category_lv3="公园",
            typecode="", district="", business_area="", address="",
            longitude=116.31, latitude=39.92, rating=4.5, avg_price=10,
            open_time="06:00-21:00", phone="", photos=[]),
    ]
    c = SearchConstraints(persona="family", min_rating=4.0)
    rain = WeatherContext(state="rain")

    ranked_no_weather = fuse_and_rank(pois, c)
    ranked_rain = fuse_and_rank(pois, c, weather=rain)

    s_park_no = next(r.score for r in ranked_no_weather if r.poi.id == "P2")
    s_park_rain = next(r.score for r in ranked_rain if r.poi.id == "P2")
    s_mall_no = next(r.score for r in ranked_no_weather if r.poi.id == "P1")
    s_mall_rain = next(r.score for r in ranked_rain if r.poi.id == "P1")

    assert s_park_rain < s_park_no   # 公园被降
    assert s_mall_rain > s_mall_no   # 商场被加


def test_fuse_and_rank_no_weather_no_reason():
    """weather=None 时不加 weather reason。"""
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    p = POI(id="P1", name="玉渊潭公园", category_lv1="风景名胜",
            category_lv2="公园广场", category_lv3="公园",
            typecode="", district="", business_area="", address="",
            longitude=116.31, latitude=39.92, rating=4.5, avg_price=10,
            open_time="06:00-21:00", phone="", photos=[])
    c = SearchConstraints(persona="family", min_rating=4.0)
    ranked = fuse_and_rank([p], c)
    has_w = any(rs.factor.startswith("weather") for r in ranked for rs in r.reasons)
    assert has_w is False


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
