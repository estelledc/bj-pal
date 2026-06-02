"""时间轴 UI 组件（v2 升级 + P0.1 red_flags 面板）。"""

from __future__ import annotations

from typing import Callable, Optional

import streamlit as st

from agents.types import Plan
from tools.route_lookup import format_modes_compact, RouteLeg
from tools.ugc_signals import extract_red_flags

KIND_ICON = {
    "citywalk": "🚶", "meal": "🍽️", "culture": "🏛️",
    "rest": "☕", "shopping": "🛍️", "snack": "🥨",
    "depart": "🚇",
}

REROUTE_REASON_LABELS = {
    "queue": ("🚶‍♂️", "排队/拥堵"),
    "weather": ("⛅", "天气不宜"),
    "closed": ("🚫", "商家停业"),
    "user_dissent": ("👤", "用户反馈"),
    "merchant_reject": ("❌", "商家拒单"),
    "none": ("ℹ️", "其它"),
}


def render_timeline(
    plan: Plan,
    on_dissent: Optional[Callable[[int], None]] = None,
    *,
    show_red_flags: bool = False,
):
    """v2 升级版时间轴。

    Args:
        plan: Plan
        on_dissent: 用户点"换一个"按钮的回调（接收 step_index）；None 时不显示按钮
    """
    for i, s in enumerate(plan.steps):
        # 1) 上一步到这步的 travel：在两步之间显示
        if i > 0 and s.travel_time_min > 0 and s.travel_options:
            _render_travel_segment(s)

        # 2) Step 主卡片
        with st.container(border=True):
            icon = KIND_ICON.get(s.kind, "📍")
            cols = st.columns([1, 5, 1])
            with cols[0]:
                st.markdown(f"### {icon}")
                st.caption(s.start_time)
                st.caption(f"停 {s.duration_min}min")
            with cols[1]:
                title_parts = [f"**{s.step_index}. {s.poi_name}**"]
                if s.is_rerouted:
                    emoji, lbl = REROUTE_REASON_LABELS.get(s.reroute_reason or "none",
                                                             ("🔄", "REROUTED"))
                    title_parts.append(f":red[{emoji} {lbl}]")
                st.markdown(" ".join(title_parts))
                st.caption(f"`{s.kind}`")
                if s.rationale:
                    st.markdown(f"_{s.rationale}_")
                # v2 改 3：booking 字段（座位 / 菜单 / 照片）
                if s.booking and isinstance(s.booking, dict):
                    _render_booking_card(s.booking)
                if s.risk_tags:
                    st.caption(f"⚠️ risk: {', '.join(s.risk_tags)}")
                # P0.1 red flags：吐槽面板（信号 2，5/5 一致：必须把吐槽点出来）
                if show_red_flags and s.poi_name and s.kind != "depart":
                    _render_red_flags(s.poi_name)
            with cols[2]:
                if on_dissent and s.poi_id and s.kind != "depart":
                    st.markdown("&nbsp;", unsafe_allow_html=True)
                    if st.button("换一个", key=f"dissent_{s.step_index}",
                                 help="主动 reroute 这一步", use_container_width=True):
                        on_dissent(i)


def _render_travel_segment(s):
    """步骤之间的 travel 段：显示推荐模式 + 4 模式 emoji 紧凑对比。"""
    # 重建 RouteLeg 字典用于 format_modes_compact
    legs = {}
    for mode, opt in (s.travel_options or {}).items():
        legs[mode] = type("FakeLeg", (), {  # 简易对象
            "duration_min": opt.get("duration_min", 0),
            "distance_m": opt.get("distance_m", 0),
        })
    compact = format_modes_compact(legs)
    chosen = s.mode_to_here
    chosen_label = {"walking": "步行", "bicycling": "骑行", "driving": "驾车", "transit": "公交"}.get(chosen, chosen)
    st.markdown(
        f"<div style='text-align:center; color:#888; font-size:12px; "
        f"padding: 4px 0; border-left: 2px dotted #C8302D; "
        f"margin-left: 28px; padding-left: 18px;'>"
        f"↓ <b>{chosen_label} {s.travel_time_min}min</b> ({s.travel_distance_m}m) "
        f"&nbsp;·&nbsp; 4 模式：{compact}</div>",
        unsafe_allow_html=True,
    )


def _render_red_flags(poi_name: str):
    """P0.1 吐槽面板：每张 POI 卡片显示 1 条最关键吐槽（即使整体推荐）。

    - confidence < 0.5 或 age > 30 → 标灰、降权但保留可见
    - 显示原文 + age_days + source_count + 冲突信号数
    """
    flags = extract_red_flags(poi_name=poi_name, top_k=1)
    if not flags:
        return
    f = flags[0]
    bg = "#fff7ec" if not f["should_dim"] else "#f1f1f1"
    border = "#C8302D" if not f["should_dim"] else "#bbb"
    text_color = "#7b3a1c" if not f["should_dim"] else "#777"
    age_label = f"{f['age_days']} 天前"
    src_label = f"{f['source_count']} 条来源"
    conflict_label = f" · 含 {f['conflicting_signals']} 条相反评价" if f["conflicting_signals"] else ""
    dim_tag = "（已降权）" if f["should_dim"] else ""
    st.markdown(
        f"<div style='background:{bg}; border-left:3px solid {border}; "
        f"padding:6px 10px; margin-top:6px; border-radius:4px; "
        f"font-size:12px; color:{text_color};'>"
        f"⚠ <b>1 条关键吐槽 [{f['aspect_type']}]</b> {dim_tag}"
        f"<br/>"
        f"<i>『{f['evidence_summary'][:80]}』</i>"
        f"<br/>"
        f"<span style='opacity:0.7;'>"
        f"conf {f['confidence']:.2f} → 时效衰减后 {f['decayed_confidence']:.2f} "
        f"· {age_label} · {src_label}{conflict_label}"
        f"</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _render_booking_card(booking: dict):
    """显示 mock_book 真实感字段。"""
    parts = []
    if booking.get("seat_no"):
        parts.append(f"🪑 {booking['seat_no']}")
    if booking.get("waiting_parties") is not None:
        wait_emoji = "🟢" if booking["waiting_parties"] == 0 else "🟡"
        parts.append(f"{wait_emoji} 等位 {booking['waiting_parties']} 桌")
    if booking.get("latency_ms"):
        parts.append(f"⏱️ {booking['latency_ms']:.0f}ms")
    if parts:
        st.caption("　·　".join(parts))
    if booking.get("menu_preview"):
        with st.expander(f"📋 菜单预览（{len(booking['menu_preview'])} 项）", expanded=False):
            for m in booking["menu_preview"]:
                tag = m.get("tag") or ""
                st.markdown(
                    f"- **{m['name']}** ¥{m['price']} "
                    + (f":violet-badge[:material/star: {tag}]" if tag else "")
                )
    if booking.get("photos"):
        cols = st.columns(min(len(booking["photos"]), 3))
        for i, url in enumerate(booking["photos"][:3]):
            with cols[i]:
                st.image(url, use_container_width=True)
