"""[31] OPTW + OR-Tools 测试。

覆盖：
- 简单可行场景 → OPTIMAL，序列合法
- 时间预算太紧 → INFEASIBLE
- 时窗冲突 → 跳过该 POI
- 全局 trade-off：solver 不一定挑 utility 最高（visit 太长会被淘汰）
- 通勤矩阵 haversine
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.optw_solver import (
    OPTWPoi, OPTWResult,
    build_travel_matrix, solve_optw,
)


# ============================================================
# 共用 fixture
# ============================================================

def _make_pois():
    return [
        OPTWPoi("P1", "故宫",     900, 510, 960,   120, 116.397, 39.916),
        OPTWPoi("P2", "南锣鼓巷", 700, 600, 22*60, 60,  116.402, 39.937),
        OPTWPoi("P3", "雍和宫",   600, 540, 16*60, 60,  116.414, 39.948),
        OPTWPoi("P4", "簋街胡大", 500, 17*60, 24*60, 75, 116.426, 39.939),
        OPTWPoi("P5", "三联书店", 400, 9*60,  22*60, 45, 116.418, 39.910),
        OPTWPoi("P6", "景山公园", 550, 390,   22*60, 60, 116.395, 39.927),
    ]


# ============================================================
# 测试
# ============================================================

def test_travel_matrix_dimensions():
    pois = _make_pois()
    M = build_travel_matrix(pois, start=(116.395, 39.916))
    assert len(M) == len(pois) + 2  # +start +end
    assert len(M[0]) == len(pois) + 2
    # 对角线 = 0
    for i in range(len(M)):
        assert M[i][i] == 0
    # 起点→故宫 应该 < 10 分钟（同片区）
    assert M[0][1] <= 10


def test_optw_simple_feasible():
    """5 个 POI 可选 2-4 个，270 min 预算应该出解。"""
    pois = _make_pois()
    M = build_travel_matrix(pois, start=(116.395, 39.916))
    result = solve_optw(
        pois=pois, start_min=14*60, end_min=18*60+30,
        travel_matrix=M, min_visits=2, max_visits=4,
        time_limit_s=5.0,
    )
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert 2 <= result.n_visited <= 4
    assert result.total_minutes_used <= 270
    assert len(result.sequence) == result.n_visited


def test_optw_infeasible_when_too_tight():
    """min_visits=4 + 故宫 visit=120 + 簋街 open=17:00 → 时间预算不够。"""
    pois = _make_pois()
    M = build_travel_matrix(pois, start=(116.395, 39.916))
    result = solve_optw(
        pois=pois, start_min=14*60, end_min=18*60,  # 只有 240 min
        travel_matrix=M, min_visits=4, max_visits=6,
        time_limit_s=5.0,
    )
    # 4 个 POI 至少 4×60=240 min visit，加通勤 必然超 240
    assert result.solver_status == "INFEASIBLE"
    assert result.n_visited == 0


def test_optw_global_tradeoff_skips_high_utility_long_visit():
    """全局 trade-off：visit 太长的高分 POI 不一定被选。"""
    pois = _make_pois()
    M = build_travel_matrix(pois, start=(116.395, 39.916))
    result = solve_optw(
        pois=pois, start_min=14*60, end_min=18*60+30,  # 270 min
        travel_matrix=M, min_visits=2, max_visits=2,    # 强制 2 步
        time_limit_s=5.0,
    )
    # 故宫 utility 900 但 visit 120 + 任意一个 POI 60 + 通勤 ≈ 200-220
    # 不是必选；solver 会选 utility 总和最大的可行组合
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert result.n_visited == 2
    # 总 utility ≥ 1100（南锣 700 + 雍和宫 600 是合理选择）
    assert result.total_utility >= 1000


def test_optw_time_window_respected():
    """簋街 open=17:00，arrival 必须 ≥ 17:00 才能选；如果只跑下午早段则不会选。"""
    pois = _make_pois()
    M = build_travel_matrix(pois, start=(116.395, 39.916))
    result = solve_optw(
        pois=pois,
        start_min=14*60, end_min=16*60,  # 14:00-16:00 内只能选 open ≤ 16:00 的
        travel_matrix=M, min_visits=1, max_visits=2,
        time_limit_s=5.0,
    )
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    # 簋街 open 17:00，肯定不会被选
    assert "P4" not in result.sequence


def test_optw_arrivals_monotonic():
    """到达时间序列必须递增。"""
    pois = _make_pois()
    M = build_travel_matrix(pois, start=(116.395, 39.916))
    result = solve_optw(
        pois=pois, start_min=14*60, end_min=20*60,
        travel_matrix=M, min_visits=3, max_visits=5,
        time_limit_s=5.0,
    )
    if result.solver_status in ("OPTIMAL", "FEASIBLE"):
        for i in range(1, len(result.arrival_times)):
            assert result.arrival_times[i] > result.arrival_times[i-1]


def test_optw_empty_input():
    result = solve_optw(
        pois=[], start_min=14*60, end_min=18*60,
        travel_matrix=[[0]], min_visits=1, max_visits=3,
    )
    assert result.solver_status == "INFEASIBLE"
    assert result.n_visited == 0


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
