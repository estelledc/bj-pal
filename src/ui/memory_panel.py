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

KIND_LABELS = {
    "preference": "偏好",
    "dislike": "禁忌",
    "fact": "事实",
    "identity": "身份",
}

MEMORY_PANEL_TITLE = "记忆"
MEMORY_PANEL_EMPTY_TITLE = "记忆（暂无）"
MEMORY_ROW_COLUMNS = [4.55, 1.55]
MEMORY_FORGET_BUTTON_LABEL = "忘记"
MEMORY_FORGET_BUTTON_USE_CONTAINER_WIDTH = True

MEMORY_KEY_PREFIX_LABELS = {
    "diet": "饮食",
    "taste": "口味",
    "preference": "偏好",
    "avoid": "避开",
    "risk": "风险",
    "scene": "场景",
    "party": "同行",
    "area": "片区",
    "poi": "地点",
}

MEMORY_VALUE_LABELS = {
    "advance_booking_needed": "需要提前预约",
    "advance_booking_required": "需要预约",
    "allergy": "过敏",
    "buffet": "自助餐",
    "child_friendly": "适合孩子",
    "citywalk": "散步游逛",
    "closed_often": "经常闭店",
    "coffee": "咖啡",
    "couple": "情侣同行",
    "crowded": "拥挤",
    "dessert": "甜品",
    "drink": "饮品",
    "elderly_friendly": "适合长辈",
    "expensive": "价格偏高",
    "fruit": "水果",
    "friends_gathering": "朋友聚会",
    "halal": "清真",
    "indoor": "室内",
    "kid_friendly": "亲子友好",
    "light": "清淡",
    "light_diet": "清淡饮食",
    "loud": "嘈杂",
    "low_oil": "少油",
    "low_purine": "低嘌呤",
    "low_sugar": "低糖",
    "meat": "肉类",
    "medical_diet_risk": "健康相关饮食风险",
    "dairy_free": "不吃奶制品",
    "gluten_free": "无麸质",
    "hives": "荨麻疹",
    "low_fat": "低脂",
    "no_lactose": "乳糖不耐受",
    "no_alcohol": "不喝酒",
    "no_beef": "不吃牛肉",
    "no_crab": "不吃螃蟹",
    "no_fish": "不吃鱼",
    "no_mutton": "不吃羊肉",
    "no_parking": "停车困难",
    "no_pork": "不吃猪肉",
    "no_reservation": "无法预约",
    "no_seafood": "不吃海鲜",
    "no_shellfish": "不吃贝壳类海鲜",
    "no_shrimp": "不吃虾",
    "no_spicy": "不吃辣",
    "nut_allergy": "坚果过敏",
    "outdoor": "户外",
    "parking_extreme": "停车极难",
    "peanut_allergy": "花生过敏",
    "photo": "适合拍照",
    "queue": "排队",
    "queue_long": "排队较久",
    "quiet": "安静",
    "raw_seafood": "生食海鲜",
    "reservation_required": "需要预约",
    "smoky_room": "烟味环境",
    "sour": "酸口",
    "sour_food": "酸口食物",
    "spicy": "辣味",
    "vegetarian": "素食",
    "vinegar_flavor": "醋味",
    "watermelon": "西瓜",
    "weekend_long_queue": "周末排队较久",
    "window_seat": "靠窗座位",
    "with_child": "带孩子",
    "with_elderly": "带长辈",
    "yogurt": "酸奶",
    "urticaria": "荨麻疹",
}

UNKNOWN_MEMORY_VALUE_LABELS = {
    "diet": "其他饮食约束",
    "taste": "其他口味偏好",
    "preference": "其他偏好",
    "avoid": "其他规避项",
    "risk": "其他风险",
    "scene": "其他场景偏好",
    "party": "其他同行偏好",
    "area": "其他片区偏好",
    "poi": "其他地点偏好",
}


def display_memory_key(mem_key: str) -> str:
    """Return a Chinese-only display label for an internal memory key."""
    prefix, sep, value = str(mem_key or "").partition(":")
    if not sep:
        return _display_memory_value(prefix)
    prefix_label = MEMORY_KEY_PREFIX_LABELS.get(prefix, "记忆")
    return f"{prefix_label}：{_display_memory_value(value, prefix=prefix)}"


def _display_memory_value(value: str, *, prefix: str = "") -> str:
    token = str(value or "").strip()
    if not token:
        return UNKNOWN_MEMORY_VALUE_LABELS.get(prefix, "其他记忆")
    label = MEMORY_VALUE_LABELS.get(token)
    if label:
        return label
    if any("\u4e00" <= ch <= "\u9fff" for ch in token):
        return token.replace("_", "、")
    return UNKNOWN_MEMORY_VALUE_LABELS.get(prefix, "其他记忆")


def render_memory_panel(user_id: str) -> None:
    """sidebar 折叠面板：显示 AI 当前记得的偏好，提供 forget 按钮。"""
    if not user_id:
        return

    prefs = get_preferences(user_id, apply_decay=True)
    if not prefs:
        with st.expander(MEMORY_PANEL_EMPTY_TITLE, expanded=False):
            st.caption("还没有偏好沉淀。多用几次，系统会逐步记住你的偏好和禁忌。")
        return

    n = len(prefs)
    avg_conf = sum(p.confidence for p in prefs) / n if n else 0.0
    with st.expander(f"{MEMORY_PANEL_TITLE} · {n} 条（平均可靠度 {avg_conf:.0%}）", expanded=False):
        st.caption(
            "跨次使用保留偏好；30 天没复现的内容会逐步淡化。你可以单条忘记。"
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
            kind_label = KIND_LABELS.get(kind, "记忆")
            st.markdown(f"**{badge} {kind_label}**")
            for p in sorted(items, key=lambda x: x.confidence, reverse=True):
                display_label = display_memory_key(p.mem_key)
                col_label, col_btn = st.columns(MEMORY_ROW_COLUMNS, gap="small")
                with col_label:
                    decayed = "（已淡化）" if p.confidence < 0.5 else ""
                    st.markdown(
                        f"<span style='color:{color}'>{display_label}</span> "
                        f"<span style='color:#9ca3af;font-size:11px'>"
                        f"已记录 · 可靠度 {p.confidence:.2f} {decayed}"
                        f"</span>",
                        unsafe_allow_html=True,
                    )
                with col_btn:
                    btn_key = f"forget_{kind}_{p.mem_key}"
                    if st.button(
                        MEMORY_FORGET_BUTTON_LABEL,
                        key=btn_key,
                        help=f"忘记 {display_label}",
                        use_container_width=MEMORY_FORGET_BUTTON_USE_CONTAINER_WIDTH,
                    ):
                        forget(user_id, p.mem_key, kind=kind)
                        st.rerun()

        st.markdown("")
        if st.button("清空记忆", key="forget_all_btn",
                     help="清空当前用户的所有记忆"):
            n_cleared = forget_all(user_id)
            st.success(f"已清空 {n_cleared} 条记忆")
            st.rerun()
