"""v2.8 D7 UI：路线可惜度面板（opportunity cost）。

回答"如果选 B 而不是 A，我损失什么？"
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.opportunity_cost import (  # noqa: E402
    OpportunityReport,
    StepOpportunity,
    compute_plan_opportunity,
)
from agents.types import Plan, UserPreferences  # noqa: E402


def _regret_color(regret: float) -> tuple[str, str]:
    """returns (color, label)."""
    if regret >= 0.6:
        return ("#ef4444", "高可惜")
    if regret >= 0.3:
        return ("#f59e0b", "中等可惜")
    return ("#10b981", "选得稳")


def render_opportunity_panel(plan: Plan, *, prefs: UserPreferences | None = None,
                              expanded: bool = False) -> None:
    """渲染路线可惜度面板。"""
    try:
        report = compute_plan_opportunity(plan, prefs=prefs)
    except Exception as exc:
        st.warning(f"路线可惜度计算失败：{type(exc).__name__}: {exc}")
        return

    if not report.steps:
        return

    n_high = len(report.high_regret_steps)
    avg = report.total_regret / len(report.steps)
    summary_color, summary_label = _regret_color(avg)

    title = (
        f"💔 路线可惜度 · 平均 {avg:.0%} · {summary_label}"
        + (f" · ⚠ {n_high} 步可换" if n_high else " · 全程稳")
    )
    with st.expander(title, expanded=expanded):
        st.caption(
            "v2.8 D7 · 每一步 chosen vs 同类下一名候选的对比。"
            "⚠ 高可惜的步骤值得让用户主动确认是否切换。"
        )

        for op in report.steps:
            _render_step_row(op)

        st.markdown("")
        st.markdown(
            f"**总可惜度** = {report.total_regret:.2f}（{len(report.steps)} 步求和）"
        )
        if report.high_regret_steps:
            st.markdown(
                f"**建议复核**：第 {', '.join(str(i) for i in report.high_regret_steps)} 步"
            )


def _render_step_row(op: StepOpportunity) -> None:
    color, label = _regret_color(op.regret_score)
    pct = int(op.regret_score * 100)

    col_label, col_chosen, col_alt, col_bar = st.columns([1.2, 2.5, 2.5, 2])
    with col_label:
        kind_emoji = {
            "meal": "🍜", "citywalk": "🚶", "culture": "🏛️",
            "rest": "☕", "shopping": "🛍️", "snack": "🍰",
        }.get(op.step_kind, "📍")
        st.markdown(f"**{kind_emoji} 第 {op.step_index} 步**")
    with col_chosen:
        st.markdown(f"**chosen** `{op.chosen_score:.2f}`")
        st.caption(op.chosen_name)
    with col_alt:
        st.markdown(f"**备选** `{op.alternative_score:.2f}`")
        st.caption(op.alternative_name)
    with col_bar:
        bar_html = (
            f"<div style='background:#e5e7eb;border-radius:6px;height:14px;"
            f"overflow:hidden;margin-top:4px'>"
            f"<div style='background:{color};width:{pct}%;height:100%'></div>"
            f"</div>"
            f"<div style='font-size:12px;color:{color};margin-top:2px'>"
            f"<strong>{pct}%</strong> {label}</div>"
        )
        st.markdown(bar_html, unsafe_allow_html=True)

    st.caption(op.rationale)
    st.markdown("")
