"""BJ-Pal Streamlit Web UI（v2 总集成）。

跑法：
    python3 -m streamlit run src/ui/app.py

v2 新增：
- Hero 区微信对话开场（改 10）
- 真实路由时间 + 4 模式对比（改 1 + 改 6B）
- 真实 mock：菜单 / 座位 / 照片 / 延迟（改 3）
- 多种 reroute：queue / weather / closed / user_dissent（改 4）
- 群发投票：4 头像状态（改 2）
- UGC 截图上传 + vision 抽取（改 6A）
- AddOn 主动建议（改 7）
- 朋友 4 人偏好调和（改 8）
- vs 朴素 GPT 对照视图（改 9）
- reasons 雷达图（改 11）
- 自定义主题（改 5，见 .streamlit/config.toml）
"""

from __future__ import annotations

import sys
import time
import uuid
import os
from pathlib import Path

import streamlit as st

SRC_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC_ROOT))

from agents.addon_agent import suggest_addons  # noqa: E402
from agents.group_harmony import group_rank  # noqa: E402
from agents.planner import plan as make_plan, screen_candidates  # noqa: E402
from agents.preference_mirror import (  # noqa: E402
    detect_has_elderly,
    detect_screening_mode,
)
from agents.replanner import probe_plan, replan_step  # noqa: E402
from agents.skills import describe_skills  # noqa: E402
from agents.types import UserPreferences  # noqa: E402
from agents.vision_extractor import upload_and_index  # noqa: E402
from tools.amap_search import resolve_area_center, search_pois  # noqa: E402
from tools.availability_probe import user_dissent_probe  # noqa: E402
from tools.footprint import cumulative_stats, fetch_recent_sessions  # noqa: E402
from tools.mock_book import book_restaurant  # noqa: E402
from tools.mock_message import (  # noqa: E402
    DEMO_FRIEND_GROUP,
    broadcast_to_group,
    render_im_card,
    send_via_wechat_mock,
    simulate_group_responses,
)
from tools.tool_call_log import clear_session, fetch_calls, set_session  # noqa: E402
from tools.types import POI, SearchConstraints  # noqa: E402
from ui.calibration_timeline import render_calibration_timeline_panel  # noqa: E402
from ui.map_view import render_map  # noqa: E402
from ui.memory_panel import render_memory_panel  # noqa: E402
from ui.multimodal_intake import (  # noqa: E402
    apply_multimodal_to_query,
    render_multimodal_intake,
)
from ui.opportunity_panel import render_opportunity_panel  # noqa: E402
from ui.radar import render_radar  # noqa: E402
from ui.timeline import render_timeline  # noqa: E402
from ui.trust_panel import (  # noqa: E402
    render_global_ece,
    render_member_weights_panel,
    render_trust_panel,
)


PRIMARY_WORKSPACE_COLUMNS = ("plan", "map")
SECONDARY_RESULT_TABS = ("发送", "补充材料", "诊断")
DIAGNOSTIC_LABEL = "诊断"
AGENT_SKILL_PANEL_LABEL = "Agent 能力目录"
TASK_BAR_FIELDS = ("persona", "area", "budget", "start_time", "duration", "mode", "generate")
SIDEBAR_SECTIONS = ("演示开关", "记忆与校准")
REROUTE_MEMORY_KEY = "reroute_memory_poi_names"
PRODUCT_PAGE_TITLE = "BJ-Pal · 周末闲时活动规划"
PRODUCT_KICKER = "BJ-Pal · 北京周末闲时规划"
PRODUCT_HEADLINE = "把周末半天，排成一条能出发的路线"
PRODUCT_SUBTITLE = "面向北京本地 3-5 小时闲时出行，自动统筹片区、预算、路线、排队风险和可发送话术。"

PRESETS = {
    "family": {
        "label": "家庭出行",
        "user_input": "今天下午带老婆和 5 岁娃出去玩，别离家太远，4 小时左右。老婆减脂，娃喜欢动物。",
        "prefs": dict(persona="family", party_size=3, has_child=True, child_age=5,
                      diet_flags=["light_diet"], walk_radius_km=1.5,
                      budget_per_person=120, target_start="14:00", duration_hours=4.5),
        "audience": "spouse",
        "contact": "老婆",
    },
    "friends": {
        "label": "朋友小聚",
        "user_input": "跟 4 个朋友周六下午出去玩，2 男 2 女，别太赶，能聊天。",
        "prefs": dict(persona="friends", party_size=4, walk_radius_km=2.0,
                      budget_per_person=250, target_start="14:30", duration_hours=5.0),
        "audience": "friend",
        "contact": "@群友",
    },
}

AREAS = [
    "五道营-雍和宫片区",
    "奥林匹克公园片区",
    "王府井-东单片区",
    "什刹海-鼓楼片区",
    "天安门-故宫片区",
    "景山-什刹海片区",
    "东四-本地餐饮片区",
    "798艺术区片区",
    "前门-大栅栏片区",
    "西单片区",
    "东直门-簋街片区",
    "五道口片区",
    "望京片区",
    "亮马桥片区",
    "国贸-CBD片区",
    "朝阳公园片区",
    "中国美术馆-五四大街片区",
    "牛街片区",
]
CUSTOM_AREA_OPTION = "手动输入片区"
AREA_SELECT_OPTIONS = tuple(AREAS) + (CUSTOM_AREA_OPTION,)


def resolve_area_input(selected_area: str, manual_area: str, fallback_area: str) -> str:
    """Resolve the area value passed into planning from the task bar controls."""
    if selected_area != CUSTOM_AREA_OPTION:
        return selected_area
    return manual_area.strip() or fallback_area


def resolve_llm_backend_label() -> str:
    """Return the short runtime label shown in the task bar."""
    backend = (os.environ.get("BJ_PAL_LLM") or "mock").strip().lower()
    return {
        "mock": "Mock",
        "longcat": "LongCat",
        "dpsk": "DPSK",
        "deepseek": "DeepSeek",
        "anthropic": "Anthropic",
    }.get(backend, backend or "Mock")


def collect_reroute_memory_names(plan, events=None) -> set[str]:
    """Collect POI names that have already appeared in the current plan session."""
    names: set[str] = set()
    for step in getattr(plan, "steps", []) or []:
        if getattr(step, "kind", "") == "depart":
            continue
        name = (getattr(step, "poi_name", "") or "").strip()
        if name:
            names.add(name)
    for event in events or []:
        for attr in ("failed_poi_name", "replacement_poi_name"):
            name = (getattr(event, attr, "") or "").strip()
            if name:
                names.add(name)
    return names


def _read_reroute_memory() -> set[str]:
    return set(st.session_state.get(REROUTE_MEMORY_KEY, []))


def _write_reroute_memory(names: set[str]) -> None:
    st.session_state[REROUTE_MEMORY_KEY] = sorted(names)


def _seed_reroute_memory(plan, events=None) -> None:
    _write_reroute_memory(collect_reroute_memory_names(plan, events))


def build_user_preferences(
    preset: dict,
    *,
    budget: int,
    target_start: str,
    duration_hours: float,
    raw_input: str,
) -> UserPreferences:
    """Build request preferences from preset defaults plus task bar overrides."""
    return UserPreferences(
        **{
            **preset["prefs"],
            "budget_per_person": budget,
            "target_start": target_start,
            "duration_hours": duration_hours,
            "raw_input": raw_input,
        }
    )


def main():
    st.set_page_config(
        page_title=PRODUCT_PAGE_TITLE,
        page_icon="BJ",
        layout="wide",
    )
    _inject_product_css()

    if "session_id" not in st.session_state:
        st.session_state.session_id = f"ui-{uuid.uuid4().hex[:8]}"
        clear_session(st.session_state.session_id)
    set_session(st.session_state.session_id)

    if "user_id" not in st.session_state:
        st.session_state.user_id = "demo-user-default"
    if "persona" not in st.session_state:
        st.session_state.persona = "family"

    with st.sidebar:
        st.markdown("## 高级")

        with st.expander(SIDEBAR_SECTIONS[0], expanded=False):
            enable_weather = st.checkbox("模拟小阵雨", value=True)
            enable_closed = st.checkbox("模拟商家临时停业", value=True)
            compare_with_gpt = st.checkbox("显示基线对照", value=False)
        st.session_state["enable_weather"] = enable_weather
        st.session_state["enable_closed"] = enable_closed
        st.session_state["compare_with_gpt"] = compare_with_gpt

        with st.expander(SIDEBAR_SECTIONS[1], expanded=False):
            new_uid = st.text_input(
                "user_id",
                value=st.session_state.user_id,
                help="切换 user_id 可查看不同用户记忆。",
            )
            if new_uid != st.session_state.user_id:
                st.session_state.user_id = new_uid
                st.rerun()

            render_memory_panel(st.session_state.user_id)
            render_global_ece(samples_threshold=5)

        st.caption(f"session {st.session_state.session_id[:12]}")
        if st.button("重置当前会话", use_container_width=True):
            for k in ["plan_v1", "plan_v2", "events", "card", "send_result",
                      "broadcast_responses", "addons", "screening_result",
                      "member_profiles", REROUTE_MEMORY_KEY]:
                st.session_state.pop(k, None)
            clear_session(st.session_state.session_id)
            st.rerun()

    _render_product_header()

    user_input = st.text_area(
        "这次想怎么安排",
        value=st.session_state.get("user_input", PRESETS[st.session_state.get("persona", "family")]["user_input"]),
        height=96,
        placeholder="比如：今天下午带家人逛一逛，别太远，想吃清淡一点。",
    )

    auto_mode = "screening" if detect_screening_mode(user_input) else "planning"
    persona_key, preset, area, budget, target_start, duration_hours, mode_choice, gen_btn = _render_task_bar(
        auto_mode=auto_mode,
    )
    st.session_state["mode"] = mode_choice

    if "plan_v2" not in st.session_state and "screening_result" not in st.session_state:
        with st.expander("补充材料，可选", expanded=False):
            render_multimodal_intake()

    if gen_btn:
        augmented_input = apply_multimodal_to_query(user_input)
        st.session_state.user_input = user_input
        prefs = build_user_preferences(
            preset,
            budget=budget,
            target_start=target_start,
            duration_hours=duration_hours,
            raw_input=augmented_input,
        )
        st.session_state.prefs = prefs
        st.session_state.area = area

        if mode_choice == "screening":
            st.session_state.pop("plan_v2", None)
            st.session_state.pop(REROUTE_MEMORY_KEY, None)
            with st.status("正在筛选候选", expanded=False) as status:
                result = screen_candidates(
                    user_input=augmented_input, persona=persona_key,
                    prefs=prefs, area_anchor=area,
                    category="food", top_k=8,
                )
                st.session_state.screening_result = result
                status.update(
                    label=f"已筛出 {len(result.get('candidates', []))} 个候选",
                    state="complete",
                )
        else:
            st.session_state.pop("screening_result", None)

            if compare_with_gpt:
                cl, cr = st.columns(2)
                with cl:
                    st.markdown("### BJ-Pal")
                    with st.spinner("生成方案中"):
                        p1 = make_plan(user_input=augmented_input, persona=persona_key,
                                       user_id=st.session_state.user_id, prefs=prefs,
                                       area_anchor=area)
                        st.session_state.plan_v1 = p1
                        p2, events = probe_plan(p1, prefs=prefs)
                        st.session_state.plan_v2 = p2
                        st.session_state.events = events
                        _seed_reroute_memory(p2, events)
                        addons = suggest_addons(p2, prefs)
                        st.session_state.addons = addons
                        st.session_state.card = render_im_card(p2, audience=preset["audience"])
                    _render_plan_summary(p2, label="含真实信号")
                with cr:
                    st.markdown("### 基线对照")
                    with st.spinner("生成基线中"):
                        p_base = make_plan(user_input=augmented_input, persona=persona_key,
                                           user_id=st.session_state.user_id, prefs=prefs,
                                           area_anchor=area)
                    _render_plan_summary(p_base, label="无探针调整")
                return

            with st.status("正在生成方案", expanded=False) as status:
                t0 = time.time()
                try:
                    p1 = make_plan(user_input=augmented_input, persona=persona_key,
                                   user_id=st.session_state.user_id, prefs=prefs,
                                   area_anchor=area)
                    st.session_state.plan_v1 = p1
                    status.update(label=f"初版方案完成，{len(p1.steps)} 步，{time.time()-t0:.1f}s",
                                  state="complete")
                except Exception as e:
                    status.update(label=f"生成失败：{e}", state="error")
                    st.exception(e)
                    return

            with st.status("正在检查排队、天气和商家状态", expanded=False) as status:
                time.sleep(0.4)
                p2, events = probe_plan(p1, prefs=prefs)
                st.session_state.plan_v2 = p2
                st.session_state.events = events
                _seed_reroute_memory(p2, events)
                if events:
                    status.update(label=f"已自动调整 {len(events)} 处", state="complete")
                else:
                    status.update(label="无需调整", state="complete")

            addons = suggest_addons(p2, prefs)
            st.session_state.addons = addons

            card_style = "elderly_friendly" if detect_has_elderly(user_input) else "default"
            card = render_im_card(p2, audience=preset["audience"], style=card_style)
            st.session_state.card = card
            if card_style == "elderly_friendly":
                st.info("已切换到大字号简化卡片，便于老人阅读。")

    if "screening_result" in st.session_state and st.session_state.get("mode") == "screening":
        _render_screening(st.session_state.screening_result)
        return

    if "plan_v2" in st.session_state:
        p2 = st.session_state.plan_v2
        prefs = st.session_state.prefs
        area = st.session_state.area
        center = resolve_area_center(area)

        _render_plan_overview(p2, st.session_state.get("events", []), area)

        plan_col, map_col = st.columns([1.02, 0.98], gap="large")
        with plan_col:
            _render_reroute_banner(st.session_state.get("events", []))
            st.markdown("### 今日安排")
            render_timeline(p2, on_dissent=lambda idx: _on_user_dissent(idx, prefs))
            if st.session_state.get("addons"):
                with st.expander("可选补充", expanded=False):
                    _render_addons(st.session_state.addons)

        with map_col:
            st.markdown("### 路线地图")
            render_map(p2, center=center)
            _render_map_summary(p2)

        tabs = st.tabs(list(SECONDARY_RESULT_TABS))
        with tabs[0]:
            _render_share_panel(p2, prefs, PRESETS[st.session_state.persona])
        with tabs[1]:
            _render_supporting_inputs()
        with tabs[2]:
            _render_diagnostics(p2, prefs, area)


def _inject_product_css() -> None:
    """Apply a quieter Streamlit skin for the product UI."""
    st.markdown(
        """
        <style>
          :root {
            --bjpal-ink: #1f2522;
            --bjpal-muted: #66736d;
            --bjpal-line: #d9ded8;
            --bjpal-paper: #f7f6f2;
            --bjpal-panel: #ffffff;
            --bjpal-accent: #9f3d34;
            --bjpal-accent-2: #0f766e;
          }
          .stApp {
            background: var(--bjpal-paper);
            color: var(--bjpal-ink);
          }
          [data-testid="stSidebar"] {
            background: #ecefeb;
            border-right: 1px solid var(--bjpal-line);
          }
          [data-testid="stSidebar"] h2,
          [data-testid="stSidebar"] label {
            color: var(--bjpal-ink);
          }
          .block-container {
            padding-top: 1.35rem;
            max-width: 1260px;
          }
          .bjpal-topline {
            display: flex;
            align-items: end;
            justify-content: space-between;
            gap: 1.25rem;
            border-bottom: 1px solid var(--bjpal-line);
            padding-bottom: 0.9rem;
            margin-bottom: 1rem;
          }
          .bjpal-kicker {
            color: var(--bjpal-accent);
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0;
            margin-bottom: 0.2rem;
          }
          .bjpal-title {
            color: var(--bjpal-ink);
            font-size: clamp(1.45rem, 2.5vw, 2.2rem);
            line-height: 1.08;
            font-weight: 760;
            margin: 0;
          }
          .bjpal-subtitle {
            color: var(--bjpal-muted);
            font-size: 0.92rem;
            line-height: 1.55;
            max-width: 460px;
            margin: 0;
            text-align: right;
          }
          .bjpal-taskbar-label {
            color: var(--bjpal-muted);
            font-size: 0.76rem;
            font-weight: 700;
            margin-bottom: 0.4rem;
          }
          .bjpal-runtime {
            background: rgba(15,118,110,0.08);
            border: 1px solid rgba(15,118,110,0.18);
            border-radius: 8px;
            color: var(--bjpal-muted);
            font-size: 0.8rem;
            line-height: 1.45;
            min-height: 42px;
            padding: 0.55rem 0.7rem;
            margin-top: 1.65rem;
          }
          .bjpal-run-spacer {
            height: 1.68rem;
          }
          .bjpal-summary {
            border-top: 1px solid var(--bjpal-line);
            border-bottom: 1px solid var(--bjpal-line);
            padding: 1rem 0;
            margin: 1.35rem 0 1rem;
          }
          .bjpal-summary-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.75rem;
          }
          .bjpal-metric {
            background: rgba(255,255,255,0.72);
            border: 1px solid var(--bjpal-line);
            border-radius: 8px;
            padding: 0.85rem 1rem;
            min-height: 86px;
          }
          .bjpal-metric small {
            color: var(--bjpal-muted);
            display: block;
            font-size: 0.76rem;
            margin-bottom: 0.35rem;
          }
          .bjpal-metric strong {
            color: var(--bjpal-ink);
            display: block;
            font-size: 1.15rem;
          }
          .bjpal-note {
            border-left: 3px solid var(--bjpal-accent-2);
            background: rgba(15,118,110,0.08);
            padding: 0.75rem 0.9rem;
            border-radius: 6px;
            color: var(--bjpal-ink);
            margin-bottom: 1rem;
          }
          div[data-testid="stExpander"] {
            border-color: var(--bjpal-line);
            border-radius: 8px;
            background: rgba(255,255,255,0.5);
          }
          div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 8px;
            border-color: var(--bjpal-line);
            background: var(--bjpal-panel);
          }
          .stButton > button {
            border-radius: 8px;
            min-height: 42px;
            font-weight: 650;
          }
          .stTabs [data-baseweb="tab-list"] {
            gap: 0.25rem;
            border-bottom: 1px solid var(--bjpal-line);
          }
          .stTabs [data-baseweb="tab"] {
            border-radius: 0;
            padding-left: 0.9rem;
            padding-right: 0.9rem;
          }
          @media (max-width: 760px) {
            .bjpal-topline {
              align-items: start;
              flex-direction: column;
            }
            .bjpal-subtitle {
              text-align: left;
            }
            .bjpal-summary-grid {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .bjpal-run-spacer {
              height: 0;
            }
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_product_header() -> None:
    st.markdown(
        f"""
        <section class="bjpal-topline">
          <div>
            <div class="bjpal-kicker">{PRODUCT_KICKER}</div>
            <h1 class="bjpal-title">{PRODUCT_HEADLINE}</h1>
          </div>
          <p class="bjpal-subtitle">
            {PRODUCT_SUBTITLE}
          </p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_task_bar(auto_mode: str):
    with st.container(border=True):
        st.markdown('<div class="bjpal-taskbar-label">行程参数</div>', unsafe_allow_html=True)
        col_persona, col_area, col_budget, col_start, col_duration = st.columns(
            [1, 1.35, 0.95, 0.75, 0.95]
        )
        with col_persona:
            persona_key = st.radio(
                "出行对象",
                options=list(PRESETS.keys()),
                format_func=lambda k: PRESETS[k]["label"],
                key="persona",
                horizontal=True,
            )
        preset = PRESETS[persona_key]
        with col_area:
            current_area = st.session_state.get("area", AREAS[0])
            area_index = (
                AREA_SELECT_OPTIONS.index(current_area)
                if current_area in AREA_SELECT_OPTIONS
                else AREA_SELECT_OPTIONS.index(CUSTOM_AREA_OPTION)
            )
            area_choice = st.selectbox(
                "活动片区",
                AREA_SELECT_OPTIONS,
                index=area_index,
                help="可选预设片区，也可手动输入更细的位置。",
            )
            manual_area_default = (
                current_area
                if current_area not in AREAS
                else st.session_state.get("custom_area", "")
            )
            manual_area = ""
            if area_choice == CUSTOM_AREA_OPTION:
                manual_area = st.text_input(
                    "输入片区",
                    value=manual_area_default,
                    placeholder="例如：798 艺术区、望京、三里屯",
                    key="custom_area",
                    label_visibility="collapsed",
                )
            area = resolve_area_input(area_choice, manual_area, fallback_area=AREAS[0])
        with col_budget:
            budget = st.slider(
                "单人预算",
                min_value=30,
                max_value=500,
                value=int(st.session_state.get("budget", preset["prefs"]["budget_per_person"])),
                step=10,
            )
        with col_start:
            target_start = st.text_input(
                "出发",
                value=st.session_state.get("target_start", preset["prefs"]["target_start"]),
            )
        with col_duration:
            duration_hours = st.slider(
                "游玩时长",
                min_value=2.0,
                max_value=8.0,
                value=float(st.session_state.get("duration_hours", preset["prefs"]["duration_hours"])),
                step=0.5,
                format="%.1f 小时",
            )

        col_mode, col_status, col_run = st.columns([1.4, 1.2, 0.9])
        with col_mode:
            mode_choice = st.radio(
                "决策方式",
                options=["planning", "screening"],
                format_func=lambda k: {
                    "planning": "自动排好动线",
                    "screening": "只给候选清单",
                }[k],
                index=(0 if auto_mode == "planning" else 1),
                horizontal=True,
            )
        with col_status:
            backend = resolve_llm_backend_label()
            st.markdown(
                f"<div class='bjpal-runtime'>LLM: <b>{backend}</b><br/>"
                f"片区: <b>{area}</b></div>",
                unsafe_allow_html=True,
            )
        with col_run:
            st.markdown("<div class='bjpal-run-spacer'></div>", unsafe_allow_html=True)
            gen_btn = st.button("生成安排", type="primary", use_container_width=True)

    st.session_state.area = area
    st.session_state.budget = budget
    st.session_state.target_start = target_start
    st.session_state.duration_hours = duration_hours
    return persona_key, preset, area, budget, target_start, duration_hours, mode_choice, gen_btn


def build_plan_snapshot(plan, events=None) -> dict:
    """Return compact metrics for the result header."""
    steps = list(getattr(plan, "steps", []) or [])
    real_steps = [s for s in steps if getattr(s, "kind", "") != "depart"]
    travel_minutes = sum(int(getattr(s, "travel_time_min", 0) or 0) for s in steps)
    reroute_count = len(events or []) if events is not None else sum(
        1 for s in steps if getattr(s, "is_rerouted", False)
    )
    first_time = getattr(real_steps[0], "start_time", "") if real_steps else ""
    last_time = getattr(real_steps[-1], "start_time", "") if real_steps else ""
    travel_label = "基本不走路" if travel_minutes <= 0 else f"{travel_minutes} 分钟路上"
    return {
        "stop_count": len(real_steps),
        "travel_minutes": travel_minutes,
        "reroute_count": reroute_count,
        "first_time": first_time,
        "last_time": last_time,
        "travel_label": travel_label,
    }


def _render_plan_overview(plan, events, area: str) -> None:
    snap = build_plan_snapshot(plan, events)
    reroute_label = "无需调整" if snap["reroute_count"] == 0 else f"已调整 {snap['reroute_count']} 处"
    time_window = (
        f"{snap['first_time']} 开始"
        if not snap["last_time"] or snap["first_time"] == snap["last_time"]
        else f"{snap['first_time']} 到 {snap['last_time']}"
    )
    st.markdown(
        f"""
        <section class="bjpal-summary">
          <div class="bjpal-summary-grid">
            <div class="bjpal-metric"><small>片区</small><strong>{area}</strong></div>
            <div class="bjpal-metric"><small>节奏</small><strong>{snap['stop_count']} 个停靠点 · {snap['travel_label']}</strong></div>
            <div class="bjpal-metric"><small>时间</small><strong>{time_window}</strong></div>
            <div class="bjpal-metric"><small>履约检查</small><strong>{reroute_label}</strong></div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_reroute_banner(events) -> None:
    if not events:
        return
    with st.expander(f"已为你避开 {len(events)} 个风险点", expanded=True):
        for ev in events:
            reason_label = {
                "queue": "排队或拥堵",
                "weather": "天气不适合",
                "closed": "商家可能停业",
                "user_dissent": "用户反馈",
                "merchant_reject": "商家拒单",
            }.get(ev.reason, ev.reason)
            st.markdown(
                f"**{reason_label}**：{ev.failed_poi_name} → {ev.replacement_poi_name}"
            )
            for evidence in ev.evidence[:2]:
                st.caption(evidence)


def _render_map_summary(plan) -> None:
    snap = build_plan_snapshot(plan, st.session_state.get("events", []))
    first_stop = next(
        (s for s in getattr(plan, "steps", []) if getattr(s, "kind", "") != "depart"),
        None,
    )
    last_stop = next(
        (s for s in reversed(getattr(plan, "steps", [])) if getattr(s, "kind", "") != "depart"),
        None,
    )
    cols = st.columns(3)
    cols[0].metric("停靠点", snap["stop_count"])
    cols[1].metric("路上", snap["travel_label"])
    cols[2].metric("调整", snap["reroute_count"])
    if first_stop and last_stop:
        st.caption(f"起点：{first_stop.poi_name} · 终点：{last_stop.poi_name}")


def _render_share_panel(plan, prefs, preset: dict) -> None:
    card = st.session_state.card
    st.markdown("### 可直接发送的版本")
    st.text_area(
        "消息预览",
        value=f"{card.title}\n\n{card.body}",
        height=260,
        label_visibility="collapsed",
    )

    col_send, col_book = st.columns(2)
    with col_send:
        if st.button("发送给 " + preset["contact"], type="primary", use_container_width=True):
            send_via_wechat_mock(card, preset["contact"])
            st.success(f"已发送给 {preset['contact']}")
            time.sleep(0.8)
            st.info(f"{preset['contact']}：OK，就这么定吧")
    with col_book:
        if st.button("模拟预订", use_container_width=True):
            _confirm_book(plan, prefs, preset)

    if st.session_state.persona == "friends":
        st.markdown("### 群内确认")
        if st.button("发到群里征求意见", use_container_width=True):
            _handle_group_broadcast(plan)
        if "broadcast_responses" in st.session_state:
            _render_broadcast_panel(st.session_state.broadcast_responses, plan, prefs)
            if st.session_state.get("member_profiles"):
                render_member_weights_panel(st.session_state.member_profiles)

    st.caption("当前是演示环境：发送、预订和下单都调用 mock 接口。")


def _render_supporting_inputs() -> None:
    st.markdown("### 补充材料")
    st.caption("这里保留原来的用户侧能力：文本/截图抽偏好，以及点评截图入库。")
    render_multimodal_intake()
    _render_ugc_upload_panel()


def _render_ugc_upload_panel() -> None:
    with st.expander("上传点评截图并写入本地信号库", expanded=False):
        uploaded = st.file_uploader(
            "上传大众点评 / 美团 / 小红书截图",
            type=["jpg", "jpeg", "png"],
            key="ugc_index_upload",
        )
        if not uploaded:
            st.caption("上传后会抽取 area_anchor、poi_name 和 aspect，并加入 SQLite。")
            return

        if st.button("抽取并入库", type="primary", key="ugc_index_btn"):
            with st.spinner("正在抽取并写入本地库"):
                try:
                    image_bytes = uploaded.read()
                    extracted, n = upload_and_index(
                        image_bytes,
                        image_mime=uploaded.type or "image/jpeg",
                    )
                except Exception as exc:
                    st.error(f"抽取失败：{type(exc).__name__}: {exc}")
                    return

            st.success(f"已写入 {n} 条信号")
            st.markdown(f"**片区**：`{extracted.get('area_anchor') or '?'}`")
            st.markdown(f"**地点**：`{extracted.get('poi_name') or '?'}`")
            for aspect in extracted.get("aspects", [])[:8]:
                sentiment = aspect.get("sentiment", "mixed")
                label = {"positive": "正向", "negative": "风险", "mixed": "混合"}.get(
                    sentiment, sentiment
                )
                st.caption(
                    f"{label} / {aspect.get('aspect_type', '?')} / "
                    f"{aspect.get('confidence', 0):.2f} · "
                    f"{aspect.get('evidence_summary', '')}"
                )


def _handle_group_broadcast(plan) -> None:
    card = render_im_card(plan, audience="friend")
    broadcast_to_group(card, DEMO_FRIEND_GROUP)
    with st.spinner("等待群成员响应"):
        time.sleep(0.6)
        responses = simulate_group_responses(plan, DEMO_FRIEND_GROUP, force_one_dissent=True)
        st.session_state.broadcast_responses = responses
        from agents.group_dynamics import profile_group
        history_by_member = {r.contact: [r] for r in responses}
        first_resp = min(
            (r for r in responses if r.reply_at_ms > 0),
            key=lambda r: r.reply_at_ms, default=None,
        )
        st.session_state.member_profiles = profile_group(
            DEMO_FRIEND_GROUP,
            history_by_member,
            first_responder=first_resp.contact if first_resp else None,
        )


def _render_diagnostics(plan, prefs, area: str) -> None:
    st.markdown("### 系统诊断")
    st.caption("这些信息用于评测和调试，默认不进入用户决策视图。")
    render_trust_panel(plan, expanded=False)
    render_opportunity_panel(plan, prefs=prefs, expanded=False)
    render_calibration_timeline_panel(window_size=10, expanded=False)
    _render_top_pick_radar(area, prefs)
    _render_agent_skills_panel()

    with st.expander("Tool Call Trace", expanded=False):
        calls = fetch_calls(session_id=st.session_state.session_id, limit=200)
        if calls:
            import pandas as pd
            df_rows = [{
                "time": c["timestamp"][11:19],
                "tool": c["tool_name"],
                "status": c["status"],
                "latency(ms)": round(c["latency_ms"], 1),
                "params": (c["params_json"] or "")[:80],
            } for c in calls]
            df = pd.DataFrame(df_rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.caption("还没有工具调用记录")

    with st.expander("北京下午足迹", expanded=False):
        _render_footprint_panel()


def _render_agent_skills_panel() -> None:
    """Show reusable agent skills in diagnostics without changing the main flow."""
    with st.expander(AGENT_SKILL_PANEL_LABEL, expanded=False):
        st.caption("当前 agent 可复用能力单元。UI、测试和后续编排器都可以通过统一入口调用。")
        for skill in describe_skills():
            st.markdown(f"**{skill['label']}** · `{skill['name']}`")
            st.caption(skill["description"])
            input_keys = "、".join(skill.get("input_keys") or []) or "-"
            output_keys = "、".join(skill.get("output_keys") or []) or "-"
            st.caption(f"输入：{input_keys}　输出：{output_keys}")


# ============================================================
# Helpers
# ============================================================

def _render_footprint_panel():
    """P1.1：北京下午足迹页（数据沉淀 / 迁移成本）。

    主流程下方折叠展示，避免破坏原 sidebar / 主区结构。
    """
    stats = cumulative_stats()
    st.markdown("## 📋 我的北京下午足迹")
    st.caption(
        "100 条 AI 用户访谈：付费留存的关键是\"数据沉淀、迁移成本高、日常习惯\"。"
        "这页就是那个迁移成本——你的方案、被改的站、群投票结果都在这。"
    )
    cols = st.columns(3)
    cols[0].metric("总会话数", stats["total_sessions"])
    cols[1].metric("累计 reroute", stats["total_reroutes"])
    cols[2].metric("累计下单", stats["total_bookings"])

    sessions = fetch_recent_sessions(limit=10)
    if not sessions:
        st.info("还没有历史足迹。生成一份方案后回来看看 🌆")
        return
    st.markdown("---")
    st.markdown("### 最近 10 次")
    for entry in sessions:
        with st.container(border=True):
            head = f"**{entry.session_id}**"
            if entry.started_at:
                head += f"　·　{entry.started_at[:16]}"
            st.markdown(head)
            st.caption(entry.summary_zh)
            sub_cols = st.columns(4)
            sub_cols[0].metric("方案站数", entry.plan_steps or "-")
            sub_cols[1].metric("reroute", entry.reroute_count)
            sub_cols[2].metric("下单成功", entry.booking_success)
            sub_cols[3].metric("apology", entry.apology_card_count)
            if entry.reroute_reasons:
                st.caption(f"reroute 触发：{entry.reroute_reasons}")


def _render_screening(result: dict):
    """P0.2 筛选模式渲染：候选列表 + 理由 + 红旗。"""
    st.markdown("## 🔍 候选餐厅 / 场所（重要场合 · AI 不替您拍板）")
    st.info(
        f"💡 {result.get('decision_hint', '')}\n\n"
        f"片区 **{result.get('area_anchor', '')}** · 类目 **{result.get('category', '')}**"
    )
    candidates = result.get("candidates", [])
    if not candidates:
        st.warning("片区内无符合预算 + 评分约束的候选")
        return
    for i, c in enumerate(candidates, 1):
        with st.container(border=True):
            cols = st.columns([3, 2, 2])
            with cols[0]:
                st.markdown(f"**{i}. {c['poi_name']}**")
                st.caption(f"{c.get('category') or '?'} · {c.get('address') or '?'}")
            with cols[1]:
                st.metric("评分", f"{c.get('rating') or '?'}")
            with cols[2]:
                price = c.get("avg_price")
                st.metric("人均", f"¥{price:.0f}" if price else "?")
            if c.get("fit_reasons"):
                st.markdown("**✅ 适合：**")
                for r in c["fit_reasons"]:
                    st.markdown(f"- {r}")
            if c.get("concerns"):
                st.markdown("**⚠ 需要您留意：**")
                for r in c["concerns"]:
                    st.markdown(f"- {r}")
    st.caption(f"score 排序，AI 综合评分 + UGC 软信号 + 预算契合度筛了 top {len(candidates)}。")


def _render_plan_summary(plan, label="方案"):
    """split view 用的精简 plan 显示。"""
    st.caption(label)
    for s in plan.steps:
        marker = "🔄" if s.is_rerouted else "·"
        st.markdown(f"{marker} **{s.start_time}** {s.poi_name}")


def _render_broadcast_panel(responses, plan, prefs):
    """4 人头像状态面板（改 2）+ v2.8 D7 收敛进度 bar。"""
    st.markdown("### 📨 群发响应（4 人）")

    # v2.8 D7：收敛进度 bar
    n_total = len(responses)
    n_confirmed = sum(1 for r in responses if r.status == "confirmed")
    n_rejected = sum(1 for r in responses if r.status == "rejected")
    n_waiting = sum(1 for r in responses if r.status in ("waiting", "no_reply"))
    confirm_pct = int(100 * n_confirmed / n_total) if n_total else 0

    if confirm_pct >= 80:
        bar_color, status_text = "#10b981", "已收敛 (≥80% confirmed)"
    elif confirm_pct >= 50:
        bar_color, status_text = "#f59e0b", "进行中"
    else:
        bar_color, status_text = "#ef4444", "需 reroute"

    progress_html = f"""
    <div style='margin-bottom:12px'>
      <div style='display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px'>
        <span><strong>群体共识进度</strong>（v2.8 D7）</span>
        <span style='color:{bar_color};font-weight:bold'>{confirm_pct}% · {status_text}</span>
      </div>
      <div style='display:flex;height:18px;border-radius:9px;overflow:hidden;background:#e5e7eb'>
        <div style='background:#10b981;width:{100*n_confirmed/n_total}%' title='confirmed {n_confirmed}'></div>
        <div style='background:#ef4444;width:{100*n_rejected/n_total}%' title='rejected {n_rejected}'></div>
        <div style='background:#9ca3af;width:{100*n_waiting/n_total}%' title='waiting {n_waiting}'></div>
      </div>
      <div style='font-size:11px;color:#6b7280;margin-top:2px'>
        ✅ {n_confirmed} 通过 · ❌ {n_rejected} 否决 · ⏳ {n_waiting} 待响应
      </div>
    </div>
    """
    st.markdown(progress_html, unsafe_allow_html=True)

    cols = st.columns(len(responses))
    for col, r in zip(cols, responses):
        with col:
            with st.container(border=True):
                emoji_status = {"confirmed": "✅", "rejected": "❌",
                                "waiting": "⏳", "no_reply": "❓"}[r.status]
                st.markdown(f"### {r.avatar} {emoji_status}")
                st.caption(r.contact)
                st.caption(f"{r.reply_at_ms}ms")
                if r.reply_text:
                    st.markdown(f":small[_{r.reply_text}_]")
    # 如有 1 人否决，提示已自动 reroute
    rejected = [r for r in responses if r.status == "rejected"]
    if rejected:
        with st.container(border=True):
            st.warning(
                f"⚠️ **{rejected[0].contact} 否决**：{rejected[0].reply_text}\n\n"
                f"原因：{rejected[0].rejection_reason}"
            )
            if st.button("🔄 根据反馈重新规划", type="primary", key="rebroadcast_replan"):
                # 找 plan 中第一个 meal step
                meal_idx = next((i for i, s in enumerate(plan.steps)
                                  if s.kind == "meal"), 0)
                if meal_idx < len(plan.steps):
                    target_step = plan.steps[meal_idx]
                    poi = POI(id=target_step.poi_id or "x",
                              name=target_step.poi_name,
                              category_lv1="餐饮服务", category_lv2=None, category_lv3=None,
                              typecode=None, district=None, business_area=None, address=None,
                              longitude=None, latitude=None, rating=None, avg_price=None,
                              open_time=None, phone=None, photos=[])
                    probe_r = user_dissent_probe(
                        poi, party_size=prefs.party_size,
                        target_time=target_step.start_time,
                        reason_text=rejected[0].reply_text,
                    )
                    excluded_names = (
                        _read_reroute_memory()
                        | collect_reroute_memory_names(plan, st.session_state.get("events", []))
                    )
                    new_plan, event = replan_step(
                        plan,
                        meal_idx,
                        probe_r,
                        prefs=prefs,
                        excluded_poi_names=excluded_names,
                    )
                    st.session_state.plan_v2 = new_plan
                    st.session_state.events.append(event)
                    _write_reroute_memory(
                        excluded_names | collect_reroute_memory_names(new_plan, [event])
                    )
                    st.session_state.card = render_im_card(new_plan, audience="friend")
                    # 重置 broadcast 让用户可以再次广播
                    st.session_state.pop("broadcast_responses", None)
                    st.rerun()


def _render_addons(addons):
    """主动建议卡片列（改 7）。"""
    if not addons:
        return
    st.markdown("### 🎁 智能建议（AddOn Agent）")
    cols = st.columns(min(len(addons), 3))
    for col, a in zip(cols, addons):
        with col:
            with st.container(border=True):
                st.markdown(f"**{a.title}**")
                st.markdown(f":small[{a.description}]")
                if a.cost_estimate is not None:
                    st.caption(f"预计 ¥{a.cost_estimate:.0f}")
                st.button(a.action_label, key=f"addon_{a.kind}", use_container_width=True)


def _render_top_pick_radar(area: str, prefs: UserPreferences):
    """选当前片区第一名餐厅，画 reasons 雷达图。"""
    from tools.rank_fuse import fuse_and_rank
    constraints = SearchConstraints(
        persona=prefs.persona, has_child=prefs.has_child, child_age=prefs.child_age,
        budget_per_person=prefs.budget_per_person,
        walk_radius_km=prefs.walk_radius_km, min_rating=4.0,
    )
    candidates = search_pois(area_anchor=area, category="food",
                              constraints=constraints, limit=15)
    if not candidates:
        return
    ranked = fuse_and_rank(candidates, constraints,
                            center=resolve_area_center(area))
    if not ranked:
        return
    top = ranked[0]
    st.markdown("#### 🎯 Top 餐厅 reasons 雷达")
    st.caption(f"{top.poi.name} · score={top.score}")
    render_radar(top.reasons, title=top.poi.name)


def _on_user_dissent(step_idx: int, prefs: UserPreferences):
    """用户点"换一个"按钮——构造 user_dissent_probe + replan。"""
    p2 = st.session_state.plan_v2
    target = p2.steps[step_idx]
    excluded_names = (
        _read_reroute_memory()
        | collect_reroute_memory_names(p2, st.session_state.get("events", []))
    )
    cat_lv1 = {
        "meal": "餐饮服务", "snack": "餐饮服务", "rest": "餐饮服务",
        "shopping": "购物服务", "culture": "科教文化服务",
        "citywalk": "风景名胜",
    }.get(target.kind, "风景名胜")
    poi = POI(id=target.poi_id or "x", name=target.poi_name,
              category_lv1=cat_lv1,
              category_lv2=None, category_lv3=None, typecode=None, district=None,
              business_area=None, address=None, longitude=None, latitude=None,
              rating=None, avg_price=None, open_time=None, phone=None, photos=[])
    probe_r = user_dissent_probe(poi, party_size=prefs.party_size,
                                  target_time=target.start_time,
                                  reason_text="不想去这个，换一个")
    new_plan, event = replan_step(
        p2,
        step_idx,
        probe_r,
        prefs=prefs,
        excluded_poi_names=excluded_names,
    )
    st.session_state.plan_v2 = new_plan
    st.session_state.events.append(event)
    _write_reroute_memory(
        excluded_names | collect_reroute_memory_names(new_plan, [event])
    )
    preset = PRESETS[st.session_state.persona]
    st.session_state.card = render_im_card(new_plan, audience=preset["audience"])
    if event.replacement_poi_name:
        st.toast(f"🔄 已替换：{target.poi_name} → {event.replacement_poi_name}", icon="✨")
    else:
        st.toast(f"⚠ 找不到合适的替补（类目 {target.kind}），原方案保留", icon="⚠️")
    st.rerun()


def _confirm_book(plan, prefs, preset):
    """主菜步骤 mock 下单。"""
    meal_steps = [s for s in plan.steps if s.kind == "meal" and s.poi_id]
    if not meal_steps:
        st.warning("方案里没有 meal step")
        return
    s = meal_steps[0]
    # 拉照片
    from loader import get_conn
    conn = get_conn()
    row = conn.execute("SELECT photos_json FROM pois WHERE id = ?", (s.poi_id,)).fetchone()
    conn.close()
    photos = []
    if row:
        try:
            import json
            photo_list = json.loads(row["photos_json"] or "[]")
            photos = [p.get("url") for p in photo_list if isinstance(p, dict) and p.get("url")][:3]
        except Exception:
            pass
    book = book_restaurant(
        poi_id=s.poi_id, poi_name=s.poi_name,
        target_time=s.start_time, party_size=prefs.party_size,
        contact_name=preset["contact"], photos=photos,
    )
    if book.status == "confirmed":
        st.success(f"🎬 [演示] {book.message}")
        st.warning(
            f"⚠ 这是 mock 调用，**未真实扣款 / 未真实预订**。\n\n"
            f"模拟时间：`{book.simulated_at}`\n\n"
            f"生产对接：`{book.real_api_path}`",
            icon="ℹ️",
        )
        st.code(f"booking_id: {book.booking_id}\n座位：{book.seat_no}\n"
                f"等位：{book.waiting_parties} 桌\n"
                f"延迟：{book.latency_ms:.0f}ms\n"
                f"链接：{book.confirmation_url}\n"
                f"is_mock: {book.is_mock}")
        # 把 booking 信息回填到 step（下次刷新时 timeline 显示）
        s.booking = {
            "booking_id": book.booking_id,
            "seat_no": book.seat_no,
            "waiting_parties": book.waiting_parties,
            "latency_ms": book.latency_ms,
            "menu_preview": book.menu_preview,
            "photos": book.photos,
        }
    else:
        st.error(f"{book.status}: {book.message}")


if __name__ == "__main__":
    main()
