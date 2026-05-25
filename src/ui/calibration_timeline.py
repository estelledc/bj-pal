"""v3.1 D7 校准时序面板 — 折线图 + 直方图。

回答："随着用户用得多了，AI 校准在变好还是变差？"
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.calibration_history import (  # noqa: E402
    get_calibration_timeline,
    get_confidence_distribution,
    get_plan_count_summary,
)


def render_calibration_timeline_panel(
    *,
    window_size: int = 10,
    n_bins: int = 5,
    expanded: bool = False,
) -> None:
    """渲染校准时序面板。

    数据少（< 1 个窗口）时显示提示，不画图。
    """
    summary = get_plan_count_summary()
    n_paired = summary["n_paired"]
    title = (
        f"📈 校准时序 · {n_paired} 配对样本"
        + (f" · 全局 ECE {summary['global_ece']:.3f}" if summary["global_ece"] else "")
    )

    with st.expander(title, expanded=expanded):
        st.caption(
            f"v3.1 D7 校准时序 · plans={summary['n_plans']} · "
            f"traces={summary['n_traces']} · outcomes={summary['n_outcomes']} · "
            f"paired={n_paired} · 目标 ECE ≤ 0.15"
        )

        if n_paired < window_size:
            st.info(
                f"配对样本 {n_paired} < {window_size}，无法画时序。"
                f"跑 `python3 -m etl.seed_calibration_data --n 30` seed 一些数据。"
            )
            _render_distribution_only()
            return

        timeline = get_calibration_timeline(window_size=window_size, n_bins=n_bins)
        if not timeline:
            st.warning("时序计算失败")
            return

        # 1) ECE 时序折线（用 streamlit native line_chart）
        ece_data = {
            f"W{w.window_index}": w.ece for w in timeline
        }
        st.markdown(f"**ECE 时序**（{len(timeline)} 个窗口，每窗 {window_size} 样本）")
        st.line_chart(ece_data, height=200)

        # 2) 平均 confidence vs 平均 success：双线对照
        st.markdown("**confidence vs 实际成败**（gap 越小校准越好）")
        chart_data = {
            "mean_confidence": {f"W{w.window_index}": w.mean_confidence for w in timeline},
            "mean_actual_success": {f"W{w.window_index}": w.mean_actual_success for w in timeline},
        }
        # 转成 dataframe-like 字典：以 window 为 index
        try:
            import pandas as pd
            df = pd.DataFrame({
                "mean_confidence": [w.mean_confidence for w in timeline],
                "mean_actual_success": [w.mean_actual_success for w in timeline],
            }, index=[f"W{w.window_index}" for w in timeline])
            st.line_chart(df, height=200)
        except ImportError:
            # 降级：分别画两条
            st.line_chart(chart_data["mean_confidence"], height=120)
            st.caption("（pandas 未装，无法 overlay 两条线）")

        # 3) Confidence 分布直方图
        _render_distribution_only()

        # 4) footer：最近 3 窗口数值
        st.markdown("**最近 3 个窗口**")
        for w in timeline[-3:]:
            ok = "✓" if w.ece <= 0.15 else "⚠"
            st.markdown(
                f"- {ok} W{w.window_index}: ECE = `{w.ece:.3f}` "
                f"conf = `{w.mean_confidence:.2f}` "
                f"实际成功率 = `{w.mean_actual_success:.2f}`"
            )


def _render_distribution_only() -> None:
    """仅画 confidence 分布直方图（独立可调用）。"""
    dist = get_confidence_distribution(n_bins=10)
    if not dist:
        return
    st.markdown("**Confidence 分布**（所有 plan_trace）")
    chart = {f"{b['range_lo']:.1f}-{b['range_hi']:.1f}": b["n"] for b in dist if b["n"] > 0}
    st.bar_chart(chart, height=160)
