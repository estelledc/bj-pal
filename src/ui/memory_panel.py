"""v2.7 D6 UI: AI 记忆面板（sidebar）。

让用户看到 AI 记得的偏好 + 一键 forget。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.user_memory import (  # noqa: E402
    forget,
    forget_all,
    get_preferences,
)


KIND_BADGE = {
    "preference": ("✓", "#10b981"),
    "dislike": ("✗", "#ef4444"),
    "fact": ("·", "#6b7280"),
    "identity": ("👤", "#7c3aed"),
}


def render_memory_panel(user_id: str) -> None:
    """sidebar 折叠面板：显示 AI 当前记得的偏好，提供 forget 按钮。"""
    if not user_id:
        return

    prefs = get_preferences(user_id, apply_decay=True)
    if not prefs:
        with st.expander("🧠 AI 记得你（暂无）", expanded=False):
            st.caption("还没有偏好沉淀。多用几次 BJ-Pal，AI 就开始懂你了。")
        return

    n = len(prefs)
    avg_conf = sum(p.confidence for p in prefs) / n if n else 0.0
    with st.expander(f"🧠 AI 记得你 · {n} 条 (平均置信 {avg_conf:.0%})", expanded=False):
        st.caption(
            f"v2.7 D6 跨 session 记忆 · `user_id={user_id[:12]}` · "
            "30 天没复现的会衰减；你可以一键忘掉某条"
        )

        # 按 kind 分组
        by_kind: dict[str, list] = {}
        for p in prefs:
            by_kind.setdefault(p.kind, []).append(p)

        for kind in ("preference", "dislike", "fact", "identity"):
            items = by_kind.get(kind, [])
            if not items:
                continue
            badge, color = KIND_BADGE.get(kind, ("·", "#9ca3af"))
            st.markdown(f"**{badge} {kind}**")
            for p in sorted(items, key=lambda x: x.confidence, reverse=True):
                col_label, col_btn = st.columns([5, 1])
                with col_label:
                    decayed = "(衰减)" if p.confidence < 0.5 else ""
                    st.markdown(
                        f"<span style='color:{color}'>{p.mem_key}</span> "
                        f"<span style='color:#9ca3af;font-size:11px'>"
                        f"提及 {p.mention_count}× · 置信 {p.confidence:.2f} {decayed}"
                        f"</span>",
                        unsafe_allow_html=True,
                    )
                with col_btn:
                    btn_key = f"forget_{kind}_{p.mem_key}"
                    if st.button("🗑️", key=btn_key, help=f"忘掉 {p.mem_key}"):
                        forget(user_id, p.mem_key, kind=kind)
                        st.rerun()

        st.markdown("")
        if st.button("⚠️ 全部忘掉", key="forget_all_btn",
                     help="清空所有记忆（仅当前 user_id）"):
            n_cleared = forget_all(user_id)
            st.success(f"已清空 {n_cleared} 条记忆")
            st.rerun()
