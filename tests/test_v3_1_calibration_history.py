"""v3.1 D7 验收：calibration_history 滑窗 ECE 时序。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.calibration_history import (  # noqa: E402
    get_calibration_timeline,
    get_confidence_distribution,
    get_plan_count_summary,
)


def t1_summary_returns_counts():
    s = get_plan_count_summary()
    assert "n_plans" in s
    assert "n_traces" in s
    assert "n_outcomes" in s
    assert "n_paired" in s
    assert "global_ece" in s
    print(f"\n[1] summary: plans={s['n_plans']} traces={s['n_traces']} "
          f"outcomes={s['n_outcomes']} paired={s['n_paired']}")


def t2_timeline_window_size_matches():
    """每窗口样本数应等于 window_size。"""
    timeline = get_calibration_timeline(window_size=5)
    if not timeline:
        print("[2] 配对样本不足，跳过")
        return
    for w in timeline:
        assert w.n_samples == 5, f"窗口 {w.window_index} n={w.n_samples}"
    print(f"[2] {len(timeline)} 窗口，每窗 5 样本")


def t3_timeline_ece_bounds():
    """所有窗口 ECE ∈ [0, 1]。"""
    timeline = get_calibration_timeline(window_size=10)
    if not timeline:
        print("[3] 配对样本不足，跳过")
        return
    for w in timeline:
        assert 0.0 <= w.ece <= 1.0
        assert 0.0 <= w.mean_confidence <= 1.0
        assert 0.0 <= w.mean_actual_success <= 1.0
    print(f"[3] {len(timeline)} 窗口 ECE 全部 ∈ [0,1]")


def t4_distribution_sums_to_n():
    """直方图 n 之和 = trace 总数。"""
    dist = get_confidence_distribution(n_bins=10)
    if not dist:
        print("[4] 无 trace 数据，跳过")
        return
    total = sum(b["n"] for b in dist)
    s = get_plan_count_summary()
    assert total == s["n_traces"], f"直方图 sum {total} ≠ traces {s['n_traces']}"
    pct_sum = sum(b["pct"] for b in dist)
    assert abs(pct_sum - 1.0) < 0.01
    print(f"[4] 直方图总数 {total} 等于 trace 数 {s['n_traces']}")


def t5_timeline_to_dict_serializable():
    import json
    timeline = get_calibration_timeline(window_size=10)
    if not timeline:
        print("[5] 配对样本不足，跳过")
        return
    serialized = json.dumps([w.to_dict() for w in timeline], ensure_ascii=False)
    assert "ece" in serialized
    print(f"[5] timeline JSON OK ({len(serialized)} chars)")


def t6_seed_workflow_smoke():
    """seed_calibration_data 工具能跑通（使用最小 n）。"""
    from etl.seed_calibration_data import seed
    before = get_plan_count_summary()
    result = seed(n=2, verbose=False)
    after = get_plan_count_summary()
    assert result["n_plans"] == 2
    # outcomes 应至少增加 some
    assert after["n_outcomes"] >= before["n_outcomes"]
    print(f"[6] seed smoke: +{after['n_outcomes'] - before['n_outcomes']} outcomes")


if __name__ == "__main__":
    t1_summary_returns_counts()
    t2_timeline_window_size_matches()
    t3_timeline_ece_bounds()
    t4_distribution_sums_to_n()
    t5_timeline_to_dict_serializable()
    t6_seed_workflow_smoke()
    print("\n所有 v3.1 D7 calibration_history 验收通过！")
