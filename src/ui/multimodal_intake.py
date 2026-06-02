"""Optional text/image intake for the Streamlit UI."""

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


DEFAULT_KEY_PREFIX = "mm"


def _mm_key(key_prefix: str, name: str) -> str:
    """Build a Streamlit widget key scoped to one render location."""
    prefix = (key_prefix or DEFAULT_KEY_PREFIX).strip()
    return f"{prefix}_{name}"


def extract_text_for_ui(text: str, *, client=None) -> TextIntakeResult:
    """Extract text signals with the configured LLM, with rules as fallback."""
    return extract_from_text(text, client=client, use_llm=True)


def _sample_text_for_index(sample_idx: int) -> str:
    if sample_idx <= 0:
        return ""
    return SAMPLE_PASTES[sample_idx - 1]["text"]


def extract_image_for_ui(
    image_bytes: bytes,
    image_mime: str = "image/jpeg",
    *,
    client=None,
) -> TextIntakeResult:
    """Extract screenshot signals, falling back to the project's mock vision path."""
    from agents.vision_extractor import extract_from_image

    try:
        extracted = extract_from_image(
            image_bytes,
            image_mime=image_mime,
            client=client,
        )
        return _result_from_vision_payload(extracted, source="vision")
    except Exception:
        from agents.llm_client import MockLLMClient

        extracted = extract_from_image(
            image_bytes,
            image_mime=image_mime,
            client=MockLLMClient(),
        )
        return _result_from_vision_payload(extracted, source="vision_mock")


def _result_from_vision_payload(extracted: dict, *, source: str) -> TextIntakeResult:
    """Adapt vision schema to TextIntakeResult for shared downstream merge."""
    result = TextIntakeResult(
        area_anchor=extracted.get("area_anchor", "") or "",
        poi_name=extracted.get("poi_name", "") or "",
        taste_tags=list(extracted.get("taste_tags", []) or []),
        scene_tags=list(extracted.get("scene_tags", []) or []),
        risk_tags=list(extracted.get("risk_tags", []) or []),
        aspects=list(extracted.get("aspects", []) or []),
        source=source,
    )
    for aspect in result.aspects:
        normalized_value = aspect.get("normalized_value", {}) or {}
        result.scene_tags += normalized_value.get("scene_tags", []) or []
        result.taste_tags += normalized_value.get("taste_tags", []) or []
        result.risk_tags += normalized_value.get("risk_tags", []) or []
    result.scene_tags = list(dict.fromkeys(result.scene_tags))
    result.taste_tags = list(dict.fromkeys(result.taste_tags))
    result.risk_tags = list(dict.fromkeys(result.risk_tags))
    return result


def render_multimodal_intake(key_prefix: str = DEFAULT_KEY_PREFIX) -> None:
    """Render optional text/image context extraction."""
    with st.container(border=True):
        st.markdown("### 从文本或截图补充偏好")
        st.caption(
            "粘贴攻略、聊天记录或上传截图后，系统会把口味、片区和规避项合并进本次需求。"
        )

        tab_text, tab_image, tab_signals = st.tabs([
            "文本", "截图", "已识别",
        ])

        with tab_text:
            _render_text_tab(key_prefix)
        with tab_image:
            _render_image_tab(key_prefix)
        with tab_signals:
            _render_signals_tab()


def _render_text_tab(key_prefix: str) -> None:
    """文本输入 tab。"""
    sample_idx = st.selectbox(
        "示例",
        options=range(len(SAMPLE_PASTES) + 1),
        format_func=lambda i: "（清空）" if i == 0 else SAMPLE_PASTES[i - 1]["label"],
        key=_mm_key(key_prefix, "sample_idx"),
    )
    default_text = ""
    if sample_idx > 0:
        default_text = _sample_text_for_index(sample_idx)

    sample_applied_key = _mm_key(key_prefix, "sample_applied_idx")
    text_input_key = _mm_key(key_prefix, "text_input")
    if st.session_state.get(sample_applied_key) != sample_idx:
        st.session_state[text_input_key] = default_text
        st.session_state[sample_applied_key] = sample_idx

    text = st.text_area(
        "贴文本",
        value=default_text,
        height=140,
        placeholder="贴攻略片段、朋友圈、微信对话，或别人推荐过的店。",
        key=text_input_key,
    )

    col_extract, col_clear = st.columns([1, 1])
    with col_extract:
        if st.button("抽取偏好", type="primary", key=_mm_key(key_prefix, "extract_btn")):
            if text and text.strip():
                with st.spinner("正在用 LLM 抽取..."):
                    result = extract_text_for_ui(text)
                    st.session_state["multimodal_signals"] = result
                    if result.is_empty():
                        st.warning("没有抽到可用偏好。")
                    else:
                        st.success(
                            f"已识别 {len(result.taste_tags)} 个口味、"
                            f"{len(result.scene_tags)} 个场景、"
                            f"{len(result.risk_tags)} 个规避项"
                        )
            else:
                st.info("请先贴一段文本")
    with col_clear:
        if st.button("清空已识别", key=_mm_key(key_prefix, "clear_btn")):
            st.session_state.pop("multimodal_signals", None)
            st.info("已清空")


def _render_image_tab(key_prefix: str) -> None:
    """图片上传 tab — 复用 vision_extractor。"""
    uploaded = st.file_uploader(
        "上传点评或攻略截图",
        type=["png", "jpg", "jpeg"],
        key=_mm_key(key_prefix, "image_input"),
    )
    if uploaded is not None:
        st.image(uploaded, use_column_width=True)
        if st.button("抽取截图偏好", key=_mm_key(key_prefix, "extract_image_btn")):
            with st.spinner("正在抽取截图偏好..."):
                raw_bytes = uploaded.getvalue()
                result = extract_image_for_ui(
                    raw_bytes,
                    image_mime=uploaded.type or "image/jpeg",
                )
                st.session_state["multimodal_signals"] = result
                st.success(
                    f"已识别：{result.poi_name or '未知地点'} / "
                    f"{result.area_anchor or '未知片区'} / "
                    f"{len(result.aspects)} 条线索"
                )
                if result.source == "vision_mock":
                    st.caption("真实截图识别暂不可用，已使用离线演示结果兜底。")


def _render_signals_tab() -> None:
    """已识别信号查看 tab。"""
    sig: TextIntakeResult | None = st.session_state.get("multimodal_signals")
    if sig is None or sig.is_empty():
        st.info("还没有识别过信号。先去文本/截图 tab 抽一下。")
        return

    cols = st.columns(2)
    with cols[0]:
        st.markdown(f"**片区**：`{sig.area_anchor or '(未识别)'}`")
        st.markdown(f"**地点**：`{sig.poi_name or '(未识别)'}`")
        st.markdown(f"**来源**：`{sig.source}`")
    with cols[1]:
        if sig.taste_tags:
            st.markdown(f"**口味**：{', '.join(sig.taste_tags)}")
        if sig.scene_tags:
            st.markdown(f"**场景**：{', '.join(sig.scene_tags)}")
        if sig.risk_tags:
            st.markdown(f"**规避**：{', '.join(sig.risk_tags)}")

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
