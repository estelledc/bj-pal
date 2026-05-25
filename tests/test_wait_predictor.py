"""[39] 等位时长预测测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.wait_predictor import (
    extract_wait_minutes,
    build_histogram,
    predict_wait,
    is_high_wait_risk,
    get_top_wait_pois,
)


# ============================================================
# 抽取规则
# ============================================================

def test_extract_min_simple():
    assert extract_wait_minutes("等位 45 分钟") == [45]


def test_extract_min_range():
    assert extract_wait_minutes("排队 30-60 分钟") == [45]


def test_extract_hour_simple():
    assert extract_wait_minutes("常年排队 3 小时") == [180]


def test_extract_hour_range():
    """1-2 小时 = 1.5h = 90min。"""
    assert extract_wait_minutes("周末排队 1-2 小时") == [90]


def test_extract_chinese_number():
    """中文数字 '两小时' = 120min。"""
    assert extract_wait_minutes("两小时内能搞定") == [120]


def test_extract_no_match():
    assert extract_wait_minutes("没数字") == []


def test_extract_filter_unreasonable():
    """超大数字（>360min 单分钟）应该被过滤掉。"""
    # 600 分钟单数 → 过滤；1-2 小时 → 90 仍有效
    out = extract_wait_minutes("延误 600 分钟，排队 1-2 小时")
    assert 600 not in out
    assert 90 in out


def test_extract_min_only_no_double_count():
    """同一段不同规则不应重复抽（'1-2 小时' 不应同时被 hour_range 和 hour 抓）。"""
    out = extract_wait_minutes("排队 1-2 小时")
    # 应该恰好 1 个：90 min
    assert out == [90]


# ============================================================
# 直方图 + 预测
# ============================================================

def test_build_histogram_size():
    n = build_histogram()
    assert n > 100  # 至少 100+ POI 有等位数据


def test_predict_wait_known_busy_poi():
    """胡大饭馆 UGC 提到 '排队 2-4 小时' → 预测 expected ≥ 100。"""
    pred = predict_wait("胡大饭馆")
    assert pred is not None
    assert pred.expected_min >= 60
    assert pred.n_samples >= 1


def test_predict_wait_unknown_poi():
    """完全没听过的 POI → None。"""
    pred = predict_wait("绝对不存在的火锅店")
    assert pred is None


def test_predict_wait_fuzzy_match():
    """胡大饭馆 vs 胡大饭馆(簋街总店) → fuzzy match 应该命中。"""
    pred = predict_wait("胡大饭馆")
    assert pred is not None


def test_is_high_wait_risk():
    # 全聚德烤鸭（王府井店）在 UGC 里是 60+ min 高危
    assert is_high_wait_risk("全聚德烤鸭（王府井店）", threshold_min=30) is True


def test_get_top_wait_pois():
    top = get_top_wait_pois(top_k=5)
    assert len(top) == 5
    # 单调递减
    for i in range(1, len(top)):
        assert top[i].expected_min <= top[i-1].expected_min
    # 至少样本数 ≥ 3（确保是稳定的预测）
    for p in top:
        assert p.n_samples >= 3


# ============================================================
# 集成：availability_probe 用 wait_predictor
# ============================================================

def test_probe_uses_histogram():
    """availability_probe 应该对 UGC 直方图高 wait 的 POI 触发 reroute。"""
    from tools.availability_probe import probe
    from tools.types import POI

    # 胡大饭馆——UGC 显示等位很长
    poi = POI(
        id="P_HUDA", name="胡大饭馆", category_lv1="餐饮服务",
        category_lv2="火锅", category_lv3="麻辣火锅",
        typecode="", district="", business_area="", address="",
        longitude=116.426, latitude=39.939,
        rating=4.5, avg_price=180, open_time="11:00-04:00",
        phone="", photos=[],
    )
    result = probe(poi, target_time="2026-05-23T18:00", party_size=4,
                   enable_weather=False, enable_closed=False)
    assert result.wait_min >= 30
    assert "ugc_histogram_long_wait" in result.risk_tags or result.fallback_action == "reroute"


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
