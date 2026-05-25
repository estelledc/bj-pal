"""v2.4 D1 trust_panel — 把 plan_tracer 的 (decision, confidence, fallback) 可视化。

用途：评委 demo 时一眼看到"AI 这步 70% 确定，因为 UGC 厚度只 5 条"。

接口：
- render_trust_panel(plan)              展开式置信度面板（主区）
- render_member_weights_panel(weights)  D5 群权重小卡（broadcast 区联动）
- render_global_ece(samples_threshold)  全局 ECE 指标（侧栏 / footer）
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.plan_tracer import (  # noqa: E402
    StepTrace,
    calibration_global,
    coverage_rate,
    iter_steps,
)
from agents.types import Plan  # noqa: E402


# ============================================================
# 颜色编码 + 文案
# ============================================================

def _confidence_class(c: float) -> tuple[str, str]:
    """返回 (color_hex, label)。"""
    if c >= 0.85:
        return ("#10b981", "高置信")
    if c >= 0.70:
        return ("#f59e0b", "中等置信")
    return ("#ef4444", "需留意")


def _format_evidence_short(evidence: dict) -> str:
    """evidence dict → 一行可读简述。"""
    parts = []
    if "rationale" in evidence and evidence["rationale"]:
        rat = evidence["rationale"]
        parts.append(rat[:50] + ("..." if len(rat) > 50 else ""))
    if evidence.get("risk_tags"):
        parts.append(f"风险标签: {', '.join(evidence['risk_tags'][:3])}")
    if evidence.get("is_rerouted"):
        parts.append("已 reroute")
    if evidence.get("has_booking"):
        parts.append("已预订")
    return " · ".join(parts) if parts else "(无 evidence)"


# ============================================================
# 主面板
# ============================================================

def render_trust_panel(plan: Plan, *, expanded: bool = False) -> None:
    """渲染 plan 的置信度面板。

    数据来自 plan_tracer.iter_steps(plan.plan_id)。
    plan() 入口已自动落库；本面板只读。
    """
    traces = iter_steps(plan.plan_id)
    if not traces:
        st.info(f"📊 trust panel：未找到 plan_id={plan.plan_id} 的 trace 数据。")
        return

    cov = coverage_rate(plan.plan_id, expected_steps=len(plan.steps))
    avg_conf = sum(t.confidence for t in traces) / len(traces)
    color, label = _confidence_class(avg_conf)

    # 头部信号
    with st.expander(
        f"🛡️ AI 履约可信度面板 · 平均 {avg_conf:.0%} · {label} (覆盖 {cov:.0%})",
        expanded=expanded,
    ):
        st.caption(
            f"v2.4 D1 plan_tracer · plan_id `{plan.plan_id}` · "
            f"共 {len(traces)} 步 · 度量目标：覆盖 100% / ECE ≤ 0.15"
        )

        # 每步一行
        for t in traces:
            _render_step_row(t)

        # footer：fallback strategies（plan 级，从第一条 trace 取，所有 step 共享）
        first = traces[0]
        if first.fallback_action:
            st.markdown("---")
            st.markdown("**🛟 兜底策略**")
            for k, v in first.fallback_action.items():
                st.markdown(f"- **{k}**：{v}")


def _render_step_row(t: StepTrace) -> None:
    """单步置信度行。"""
    color, label = _confidence_class(t.confidence)
    pct = int(t.confidence * 100)

    col_label, col_bar, col_evidence = st.columns([2, 2, 5])
    with col_label:
        kind_emoji = {
            "meal": "🍜", "citywalk": "🚶", "culture": "🏛️",
            "rest": "☕", "shopping": "🛍️", "snack": "🍰", "depart": "🚖",
        }.get(t.step_kind or "", "📍")
        st.markdown(f"**{kind_emoji} 第 {t.step_index} 步**")
        st.caption(t.decision[:36])

    with col_bar:
        # 用 HTML 绘制带色 bar，比 st.progress 表现力强
        bar_html = f"""
        <div style='background:#e5e7eb;border-radius:6px;height:14px;width:100%;
                    margin-top:6px;overflow:hidden'>
          <div style='background:{color};width:{pct}%;height:100%'></div>
        </div>
        <div style='font-size:12px;margin-top:2px;color:{color}'>
          <strong>{pct}%</strong> {label}
        </div>
        """
        st.markdown(bar_html, unsafe_allow_html=True)

    with col_evidence:
        st.caption(_format_evidence_short(t.evidence))


# ============================================================
# D5 群权重小卡（接 broadcast_panel）
# ============================================================

def render_member_weights_panel(
    profiles: dict,
    *,
    title: str = "群成员模式（D5 收敛器）",
) -> None:
    """profiles: {name: MemberProfile} from group_dynamics.profile_group。"""
    if not profiles:
        return

    PATTERN_INFO = {
        "implicit_leader": ("👑", "#7c3aed", "隐性领导（升权）"),
        "vetoer": ("🚫", "#ef4444", "反复横跳（降权）"),
        "silent": ("🌫️", "#6b7280", "沉默（半权）"),
        "normal": ("✅", "#10b981", "正常"),
    }

    st.markdown(f"**{title}**")
    cols = st.columns(len(profiles))
    for col, (name, p) in zip(cols, profiles.items()):
        emoji, color, desc = PATTERN_INFO.get(p.pattern, ("👤", "#9ca3af", p.pattern))
        with col:
            st.markdown(
                f"""
                <div style='border:1px solid {color};border-radius:8px;
                            padding:8px;text-align:center'>
                  <div style='font-size:20px'>{emoji}</div>
                  <div style='font-weight:bold;color:{color}'>{name}</div>
                  <div style='font-size:11px;color:#374151'>{desc}</div>
                  <div style='font-size:13px;margin-top:4px'>权重 <strong>{p.weight}×</strong></div>
                </div>
                """,
                unsafe_allow_html=True,
            )


# ============================================================
# 全局 ECE 指标（footer / sidebar）
# ============================================================

def render_global_ece(samples_threshold: int = 5) -> None:
    """全局 ECE 指标卡 — 跨所有 plan 的 confidence 校准状态。

    样本数 < samples_threshold 时不显示（避免误导）。
    """
    cal = calibration_global(n_bins=10)
    if cal is None or cal["n_samples"] < samples_threshold:
        return

    ece = cal["ece"]
    target = 0.15
    color = "#10b981" if ece <= target else "#f59e0b" if ece <= 0.25 else "#ef4444"
    status = "达标" if ece <= target else "需校准"

    st.markdown(
        f"""
        <div style='border-left:4px solid {color};padding:8px 12px;background:#f9fafb;
                    margin-top:12px;border-radius:4px'>
          <div style='font-size:13px;color:#6b7280'>全局校准（v2.4 度量）</div>
          <div style='font-size:22px;font-weight:bold;color:{color}'>
            ECE = {ece:.3f}
          </div>
          <div style='font-size:12px;color:#374151'>
            目标 ≤ {target} · {status} · {cal["n_samples"]} 样本
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
