"""v2.5 D2 多模态首屏 — Streamlit 输入组件。

把 hero 区改成"丢任何东西给我"输入框：
- tab1: 文本（公众号片段 / 朋友圈 / 微信对话 / 口头转述）
- tab2: 图片（复用 vision_extractor）

抽取信号统一塞 st.session_state.multimodal_signals (TextIntakeResult)
后续主 query 流程通过 merge_into_user_input 注入。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.text_intake import (  # noqa: E402
    TextIntakeResult,
    extract_from_text,
    merge_into_user_input,
)


SAMPLE_PASTES = [
    {
        "label": "公众号片段：五道营周末攻略",
        "text": (
            "周末去了五道营胡同，雍和宫北边那一片现在很出片。"
            "推荐静水StillWater 的咖啡和马卡龙，环境安静适合带娃看绘本，"
            "人均 80 左右不算贵。建议中午去，下午容易排队。"
        ),
    },
    {
        "label": "朋友圈：三里屯踩雷",
        "text": "今天三里屯逛了一下午，太挤了，火锅店还排队 1 小时，下次不来了",
    },
    {
        "label": "微信对话：朋友推荐",
        "text": (
            "@王哥：南锣鼓巷有家《静水居》挺好的，咖啡好喝环境也安静\n"
            "@我：人多吗？小娃一起能去吗？\n"
            "@王哥：周末稍微挤一点，平时还行，娃可以\n"
        ),
    },
]


def render_multimodal_intake() -> None:
    """主入口：三 tab 输入区。在 hero 后、user_input 输入框前调用。"""
    with st.container(border=True):
        st.markdown("### 🪄 丢任何东西给我（v2.5 多模态首屏）")
        st.caption(
            "粘贴文章/朋友圈/微信对话，或上传截图——AI 自动抽口味、片区、想避开的东西，"
            "下面那条 query 自动带上。"
        )

        tab_text, tab_image, tab_signals = st.tabs([
            "📝 文本", "🖼️ 截图", "🔬 已识别信号",
        ])

        with tab_text:
            _render_text_tab()
        with tab_image:
            _render_image_tab()
        with tab_signals:
            _render_signals_tab()


def _render_text_tab() -> None:
    """文本输入 tab。"""
    sample_idx = st.selectbox(
        "示例（点选后会填进下方）",
        options=range(len(SAMPLE_PASTES) + 1),
        format_func=lambda i: "（清空）" if i == 0 else SAMPLE_PASTES[i - 1]["label"],
        key="mm_sample_idx",
    )
    default_text = ""
    if sample_idx > 0:
        default_text = SAMPLE_PASTES[sample_idx - 1]["text"]

    text = st.text_area(
        "贴文本",
        value=default_text,
        height=140,
        placeholder="贴公众号片段、朋友圈、微信对话、或者随便讲一句你听别人提过的店...",
        key="mm_text_input",
    )

    col_extract, col_clear = st.columns([1, 1])
    with col_extract:
        if st.button("🔮 抽取信号", type="primary", key="mm_extract_btn"):
            if text and text.strip():
                with st.spinner("正在抽取..."):
                    result = extract_from_text(text)
                    st.session_state["multimodal_signals"] = result
                    if result.is_empty():
                        st.warning("没抽到有用信号，要不换个示例试试？")
                    else:
                        st.success(
                            f"✓ 已识别 {len(result.taste_tags)} 个口味 / "
                            f"{len(result.scene_tags)} 个场景 / "
                            f"{len(result.risk_tags)} 个规避项"
                        )
            else:
                st.info("请先贴一段文本")
    with col_clear:
        if st.button("🗑️ 清空已识别信号", key="mm_clear_btn"):
            st.session_state.pop("multimodal_signals", None)
            st.info("已清空")


def _render_image_tab() -> None:
    """图片上传 tab — 复用 vision_extractor。"""
    uploaded = st.file_uploader(
        "上传一张大众点评 / 美团 / 小红书截图",
        type=["png", "jpg", "jpeg"],
        key="mm_image_input",
    )
    if uploaded is not None:
        st.image(uploaded, use_column_width=True)
        if st.button("🔮 抽取截图信号", key="mm_extract_image_btn"):
            with st.spinner("vision 抽取中..."):
                try:
                    from agents.vision_extractor import extract_from_image
                    raw_bytes = uploaded.read()
                    extracted = extract_from_image(
                        raw_bytes,
                        image_mime=f"image/{uploaded.type.split('/')[-1]}",
                    )
                    # 把 vision schema 套进 TextIntakeResult
                    result = TextIntakeResult(
                        area_anchor=extracted.get("area_anchor", ""),
                        poi_name=extracted.get("poi_name", ""),
                        aspects=extracted.get("aspects", []),
                        source="vision",
                    )
                    # 从 aspects.normalized_value 聚合 tags
                    for a in result.aspects:
                        nv = a.get("normalized_value", {}) or {}
                        result.scene_tags += nv.get("scene_tags", []) or []
                        result.taste_tags += nv.get("taste_tags", []) or []
                        result.risk_tags += nv.get("risk_tags", []) or []
                    # 去重
                    result.scene_tags = list(dict.fromkeys(result.scene_tags))
                    result.taste_tags = list(dict.fromkeys(result.taste_tags))
                    result.risk_tags = list(dict.fromkeys(result.risk_tags))

                    st.session_state["multimodal_signals"] = result
                    st.success(
                        f"✓ 已识别 POI={result.poi_name or '?'} "
                        f"片区={result.area_anchor or '?'} "
                        f"({len(result.aspects)} 条 aspect)"
                    )
                except Exception as exc:
                    st.error(f"vision 抽取失败：{type(exc).__name__}: {exc}")


def _render_signals_tab() -> None:
    """已识别信号查看 tab。"""
    sig: TextIntakeResult | None = st.session_state.get("multimodal_signals")
    if sig is None or sig.is_empty():
        st.info("还没有识别过信号。先去文本/截图 tab 抽一下。")
        return

    cols = st.columns(2)
    with cols[0]:
        st.markdown(f"**📍 片区**：`{sig.area_anchor or '(未识别)'}`")
        st.markdown(f"**🏪 POI**：`{sig.poi_name or '(未识别)'}`")
        st.markdown(f"**🔬 来源**：`{sig.source}`")
    with cols[1]:
        if sig.taste_tags:
            st.markdown(f"**🍴 口味**：{', '.join(sig.taste_tags)}")
        if sig.scene_tags:
            st.markdown(f"**🎬 场景**：{', '.join(sig.scene_tags)}")
        if sig.risk_tags:
            st.markdown(f"**⚠️ 规避**：{', '.join(sig.risk_tags)}")

    if sig.aspects:
        with st.expander(f"展开 {len(sig.aspects)} 条 aspect 原文"):
            for i, a in enumerate(sig.aspects, 1):
                emo = "✅" if a.get("sentiment") == "positive" else (
                    "⚠️" if a.get("sentiment") == "negative" else "🔸"
                )
                st.markdown(
                    f"{i}. {emo} `[{a.get('aspect_type', '?')}]` "
                    f"({a.get('confidence', 0):.2f}) {a.get('evidence_summary', '')}"
                )


# ============================================================
# 给 app.py 调：把 multimodal 信号注入 user query
# ============================================================

def apply_multimodal_to_query(base_query: str) -> str:
    """读 st.session_state.multimodal_signals，merge 进 base_query。"""
    sig: TextIntakeResult | None = st.session_state.get("multimodal_signals")
    if sig is None or sig.is_empty():
        return base_query
    return merge_into_user_input(base_query, sig)
