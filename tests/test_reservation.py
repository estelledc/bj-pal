"""[10] 古建预约规则测试。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.reservation import (
    get_rule, check_feasibility, list_reservation_required_pois, _load,
)


def test_load_rules():
    rules = _load()
    assert len(rules) >= 30  # 至少 30 条规则


def test_alias_matching():
    assert get_rule("故宫").canonical_name == "故宫博物院"
    assert get_rule("国博").canonical_name == "国家博物馆"
    assert get_rule("奥森").canonical_name == "奥林匹克森林公园"
    assert get_rule("根本不存在的景点") is None


def test_feasibility_too_soon():
    """故宫需提前 7 天，距出行仅 2 天 → 不可行。"""
    now = datetime(2026, 5, 21, 14, 0)
    target = datetime(2026, 5, 23, 14, 0)
    chk = check_feasibility("故宫", target, now=now)
    assert chk.requires_reservation is True
    assert chk.feasible is False
    assert chk.fallback_action == "reroute"
    assert "7 天" in chk.reason


def test_feasibility_open_window():
    """提前 9 天去故宫 → 释票已开放。"""
    now = datetime(2026, 5, 21, 14, 0)
    target = datetime(2026, 5, 30, 14, 0)
    chk = check_feasibility("故宫", target, now=now)
    assert chk.requires_reservation is True
    assert chk.feasible is True
    assert chk.fallback_action == "warn"


def test_feasibility_weekly_close():
    """国博周一闭馆 → 不可行。"""
    now = datetime(2026, 5, 21, 14, 0)
    target = datetime(2026, 6, 1, 14, 0)  # 周一，且 11 天后释票早就放了
    chk = check_feasibility("国家博物馆", target, now=now)
    assert chk.feasible is False
    assert chk.closes_today is True


def test_feasibility_no_reservation_needed():
    """奥森不需预约 → 永远可行。"""
    now = datetime(2026, 5, 21, 14, 0)
    target = datetime(2026, 5, 22, 14, 0)
    chk = check_feasibility("奥森", target, now=now)
    assert chk.requires_reservation is False
    assert chk.feasible is True


def test_feasibility_unknown_poi():
    """没规则的 POI → 默认放行（不阻塞 plan）。"""
    chk = check_feasibility("某神秘小店", datetime(2026, 5, 23))
    assert chk.feasible is True
    assert chk.requires_reservation is False


def test_list_reservation_pois():
    pois = list_reservation_required_pois()
    assert len(pois) >= 25
    assert "故宫博物院" in pois


# ============================================================
# 集成：probe 用预约规则触发 reroute
# ============================================================

def test_probe_blocks_unreservable_heritage():
    """故宫 5/23 出行（仅提前 2 天）→ probe 应触发 reroute。"""
    from tools.availability_probe import probe
    from tools.types import POI

    poi = POI(
        id="P_GUGONG", name="故宫博物院", category_lv1="风景名胜",
        category_lv2="博物馆", category_lv3="历史博物馆",
        typecode="", district="", business_area="", address="",
        longitude=116.397, latitude=39.916,
        rating=4.7, avg_price=60, open_time="08:30-17:00",
        phone="", photos=[],
    )
    # 注意：probe 用 datetime.now() 内部，所以这里依赖系统时间
    # 但 target_time 在过去（5/19）相对当前 5/21 算 -2 天，会被 days < 0 分支拦
    # 改用未来的近时间触发 too_soon 分支
    import datetime as dt
    soon = (dt.datetime.now() + dt.timedelta(days=2)).strftime("%Y-%m-%dT14:00")
    result = probe(poi, target_time=soon, party_size=2,
                   enable_weather=False, enable_closed=False)
    assert "reservation_required" in result.risk_tags
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
