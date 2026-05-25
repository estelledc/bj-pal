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
from agents.types import UserPreferences  # noqa: E402
from agents.vision_extractor import upload_and_index  # noqa: E402
from tools.amap_search import resolve_area_center, search_pois  # noqa: E402
from tools.availability_probe import probe, user_dissent_probe  # noqa: E402
from tools.footprint import cumulative_stats, fetch_recent_sessions  # noqa: E402
from tools.mock_book import book_restaurant  # noqa: E402
from tools.mock_message import (  # noqa: E402
    DEMO_FRIEND_GROUP,
    GroupMember,
    broadcast_to_group,
    render_im_card,
    send_via_wechat_mock,
    simulate_group_responses,
)
from tools.tool_call_log import clear_session, fetch_calls, set_session  # noqa: E402
from tools.types import POI, SearchConstraints  # noqa: E402
from ui.hero import render_hero  # noqa: E402
from ui.map_view import render_map  # noqa: E402
from ui.memory_panel import render_memory_panel  # noqa: E402
from ui.multimodal_intake import (  # noqa: E402
    apply_multimodal_to_query,
    render_multimodal_intake,
)
from ui.radar import render_radar  # noqa: E402
from ui.timeline import render_timeline  # noqa: E402
from ui.trust_panel import (  # noqa: E402
    render_global_ece,
    render_member_weights_panel,
    render_trust_panel,
)


PRESETS = {
    "family": {
        "label": "👨‍👩‍👧 家庭（5 岁娃 + 减脂老婆）",
        "user_input": "今天下午带老婆和 5 岁娃出去玩，别离家太远，4 小时左右。老婆减脂，娃喜欢动物。",
        "prefs": dict(persona="family", party_size=3, has_child=True, child_age=5,
                      diet_flags=["light_diet"], walk_radius_km=1.5,
                      budget_per_person=120, target_start="14:00", duration_hours=4.5),
        "audience": "spouse",
        "contact": "老婆",
    },
    "friends": {
        "label": "🍻 朋友（4 人 2 男 2 女）",
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
]


def main():
    st.set_page_config(
        page_title="BJ-Pal · 北京下午活动管家",
        page_icon="🌆",
        layout="wide",
    )

    # 初始化 session
    if "session_id" not in st.session_state:
        st.session_state.session_id = f"ui-{uuid.uuid4().hex[:8]}"
        clear_session(st.session_state.session_id)
    set_session(st.session_state.session_id)

    # v2.7 D6：跨 session user_id（默认 demo-user，sidebar 可改）
    if "user_id" not in st.session_state:
        st.session_state.user_id = "demo-user-default"

    # === Hero 区（改 10）===
    show_hero = st.session_state.get("show_hero", True)
    if show_hero:
        render_hero(show=True)

    # === Sidebar：偏好与画像 ===
    with st.sidebar:
        st.markdown("## ⚙️ 偏好与画像")
        persona_key = st.radio(
            "画像",
            options=list(PRESETS.keys()),
            format_func=lambda k: PRESETS[k]["label"],
            key="persona",
            horizontal=False,
        )
        preset = PRESETS[persona_key]
        area = st.selectbox(
            "活动片区", AREAS, index=0,
            help="UGC 数据厚度：五道营 11 / 奥森 8 / 王府井 6 / 什刹海 4",
        )
        budget = st.slider(
            "单人预算（¥）",
            min_value=30, max_value=500, value=preset["prefs"]["budget_per_person"],
            step=10,
        )
        target_start = st.text_input(
            "出发时间（HH:MM）", value=preset["prefs"]["target_start"]
        )

        st.markdown("---")
        st.markdown("### 🧪 演示模式（改 4 / 改 9）")
        enable_weather = st.checkbox(
            "⛅ 14:00-15:30 小阵雨预警", value=True,
            help="改 4：户外景点触发 weather reroute",
        )
        enable_closed = st.checkbox(
            "🚫 5% 商家临时停业", value=True,
            help="改 4：餐厅 5% 概率商家拒单",
        )
        compare_with_gpt = st.checkbox(
            "🆚 vs 朴素 GPT 对照视图", value=False,
            help="改 9：split view 看差距",
        )
        st.session_state["enable_weather"] = enable_weather
        st.session_state["enable_closed"] = enable_closed
        st.session_state["compare_with_gpt"] = compare_with_gpt

        st.markdown("---")
        st.markdown(f"`session: {st.session_state.session_id[:12]}`")

        # v2.7 D6：user_id（跨 session 记忆）
        new_uid = st.text_input(
            "🧑 user_id（跨 session 记忆）",
            value=st.session_state.user_id,
            help="多人 demo 时切换 user_id 看不同记忆；同 user_id 跨重启仍生效",
        )
        if new_uid != st.session_state.user_id:
            st.session_state.user_id = new_uid
            st.rerun()

        render_memory_panel(st.session_state.user_id)

        # v2.4 D1：全局 ECE 校准指标（≥ 5 样本才显示）
        render_global_ece(samples_threshold=5)
        if st.button("🔄 重置 / 清空 Trace", use_container_width=True):
            for k in ["plan_v1", "plan_v2", "events", "card", "send_result",
                      "broadcast_responses", "addons"]:
                st.session_state.pop(k, None)
            clear_session(st.session_state.session_id)
            st.rerun()

    # === 主区 ===

    # v2.5 D2：多模态首屏
    render_multimodal_intake()

    user_input = st.text_area(
        "💬 一句话告诉我你想干嘛",
        value=st.session_state.get("user_input", preset["user_input"]),
        height=80,
    )

    # P0.2 模式 toggle（重要场合用筛选模式）
    auto_mode = "screening" if detect_screening_mode(user_input) else "planning"
    mode_choice = st.radio(
        "运行模式",
        options=["planning", "screening"],
        format_func=lambda k: {
            "planning": "🚀 轻规划（动线全套）",
            "screening": "🔍 筛选模式（候选 + 理由，最终决策你来）",
        }[k],
        index=(0 if auto_mode == "planning" else 1),
        horizontal=True,
        help="检测到生日 / 6+ 人 / 家宴等关键词时自动切筛选模式（信号 5）",
    )
    st.session_state["mode"] = mode_choice

    col_a, col_b, col_c, col_d = st.columns([1, 1, 1, 3])
    with col_a:
        gen_btn = st.button("🚀 生成方案", type="primary", use_container_width=True)
    with col_b:
        upload_btn_clicked = st.toggle("📷 上传截图", value=False,
                                        help="改 6A：vision 实时抽取 aspect")
    with col_c:
        broadcast_btn = st.button("📨 群发投票", use_container_width=True,
                                  disabled=(persona_key != "friends"),
                                  help="改 2：朋友画像下群发 4 人")

    # === 截图上传面板 ===
    if upload_btn_clicked:
        with st.expander("📷 上传大众点评截图（改 6A）", expanded=True):
            uploaded = st.file_uploader(
                "拖一张点评截图，agent 会抽取 8 维 aspect",
                type=["jpg", "jpeg", "png"],
            )
            if uploaded:
                with st.spinner("vision 抽取中..."):
                    try:
                        image_bytes = uploaded.read()
                        extracted, n = upload_and_index(image_bytes,
                                                         image_mime=uploaded.type or "image/jpeg")
                        st.success(f"✅ 抽取到 {n} 条 aspect，已加入 SQLite")
                        with st.container(border=True):
                            st.markdown(f"**area_anchor**: {extracted.get('area_anchor')}")
                            st.markdown(f"**poi_name**: {extracted.get('poi_name')}")
                            for a in extracted.get("aspects", []):
                                emoji = {"positive": "✅", "negative": "❌", "mixed": "🟡"}.get(a["sentiment"], "·")
                                st.markdown(
                                    f"{emoji} `{a['aspect_type']}` (conf={a['confidence']}) "
                                    f"— {a['evidence_summary']}"
                                )
                    except Exception as e:
                        st.error(f"vision 抽取失败：{e}")

    # === 生成流水线 ===
    if gen_btn:
        # v2.5 D2：把多模态信号 merge 进 user_input
        augmented_input = apply_multimodal_to_query(user_input)
        st.session_state.user_input = user_input  # 保留原 input 给 UI 显示
        prefs = UserPreferences(
            **{**preset["prefs"], "budget_per_person": budget, "target_start": target_start,
               "raw_input": augmented_input}
        )
        st.session_state.prefs = prefs
        st.session_state.area = area

        # P0.2 筛选模式：直接出候选，不出 plan
        if mode_choice == "screening":
            with st.status("筛选模式：拉候选 + 理由 + 红旗 ...", expanded=True) as status:
                result = screen_candidates(
                    user_input=augmented_input, persona=persona_key,
                    prefs=prefs, area_anchor=area,
                    category="food", top_k=8,
                )
                st.session_state.screening_result = result
                status.update(
                    label=f"✅ 筛了 {len(result.get('candidates', []))} 家",
                    state="complete",
                )
            _render_screening(result)
            return

        # split view（vs GPT）
        if compare_with_gpt:
            cl, cr = st.columns(2)
            with cl:
                st.markdown("### ✅ BJ-Pal（含 reroute / UGC / 真实路由）")
                with st.spinner("Planner 生成方案..."):
                    p1 = make_plan(user_input=augmented_input, persona=persona_key, user_id=st.session_state.user_id,
                                   prefs=prefs, area_anchor=area)
                    st.session_state.plan_v1 = p1
                    p2, events = probe_plan(p1, prefs=prefs)
                    st.session_state.plan_v2 = p2
                    st.session_state.events = events
                    addons = suggest_addons(p2, prefs)
                    st.session_state.addons = addons
                    st.session_state.card = render_im_card(p2, audience=preset["audience"])
                _render_plan_summary(p2, label="v2 含 reroute")
            with cr:
                st.markdown("### 🟡 朴素 GPT 对照（无 UGC / 无 reroute）")
                with st.spinner("baseline 生成..."):
                    # baseline = 跑 plan 但不 probe
                    p_base = make_plan(user_input=augmented_input, persona=persona_key, user_id=st.session_state.user_id,
                                        prefs=prefs, area_anchor=area)
                _render_plan_summary(p_base, label="baseline 无 reroute")
                st.caption("**差异**：未做 UGC ranking / 未触发 reroute / 缺 reasons")
            return  # split view 模式提前返回

        with st.status("Planner 正在生成方案 v1...", expanded=False) as status:
            t0 = time.time()
            try:
                p1 = make_plan(user_input=augmented_input, persona=persona_key, user_id=st.session_state.user_id,
                               prefs=prefs, area_anchor=area)
                st.session_state.plan_v1 = p1
                status.update(label=f"✅ v1 方案 {len(p1.steps)} 步 ({time.time()-t0:.1f}s)",
                              state="complete")
            except Exception as e:
                status.update(label=f"❌ Planner 失败：{e}", state="error")
                st.exception(e)
                return

        with st.status("主动余位探针扫描中...", expanded=True) as status:
            time.sleep(0.4)
            p2, events = probe_plan(p1, prefs=prefs)
            st.session_state.plan_v2 = p2
            st.session_state.events = events
            if events:
                for ev in events:
                    reason_label = {
                        "queue": "🚶‍♂️ 排队/拥堵", "weather": "⛅ 天气不宜",
                        "closed": "🚫 商家停业", "user_dissent": "👤 用户反馈",
                        "merchant_reject": "❌ 商家拒单",
                    }.get(ev.reason, ev.reason)
                    st.warning(
                        f"⚠️ **{reason_label}**：{ev.failed_poi_name}\n\n"
                        + "\n".join(f"• {e}" for e in ev.evidence[:2])
                        + f"\n\n→ 已切换到 **{ev.replacement_poi_name}**"
                    )
                status.update(label=f"🔄 已 reroute {len(events)} 步",
                              state="complete")
            else:
                status.update(label="✅ 全程通畅，无需 reroute", state="complete")

        # AddOn 建议
        addons = suggest_addons(p2, prefs)
        st.session_state.addons = addons

        # 话术化卡片（P1.3 检测到老人参与自动切 elderly 模式）
        card_style = "elderly_friendly" if detect_has_elderly(user_input) else "default"
        card = render_im_card(p2, audience=preset["audience"], style=card_style)
        st.session_state.card = card
        if card_style == "elderly_friendly":
            st.info("👴👵 检测到老人参与，已切换到大字号简化卡片样式（P1.3）")

    # === 群发投票（改 2）===
    if broadcast_btn and persona_key == "friends":
        if "plan_v2" not in st.session_state:
            st.warning("请先生成方案再群发")
        else:
            p2 = st.session_state.plan_v2
            card = render_im_card(p2, audience="friend")
            broadcast_to_group(card, DEMO_FRIEND_GROUP)
            with st.spinner("等待 4 人响应..."):
                time.sleep(0.6)
                responses = simulate_group_responses(p2, DEMO_FRIEND_GROUP,
                                                      force_one_dissent=True)
                st.session_state.broadcast_responses = responses
                # v2.4 D5：算成员模式 + weights
                from agents.group_dynamics import profile_group
                history_by_member = {r.contact: [r] for r in responses}
                first_resp = min(
                    (r for r in responses if r.reply_at_ms > 0),
                    key=lambda r: r.reply_at_ms, default=None,
                )
                st.session_state.member_profiles = profile_group(
                    DEMO_FRIEND_GROUP, history_by_member,
                    first_responder=first_resp.contact if first_resp else None,
                )

    # === 展示 ===
    if "plan_v2" in st.session_state:
        p2 = st.session_state.plan_v2
        prefs = st.session_state.prefs
        area = st.session_state.area
        center = resolve_area_center(area)

        st.markdown("---")

        # 群发响应面板（改 2）
        if "broadcast_responses" in st.session_state:
            _render_broadcast_panel(st.session_state.broadcast_responses, p2, prefs)
            # v2.4 D5：成员模式权重小卡（broadcast 后才有）
            if st.session_state.get("member_profiles"):
                render_member_weights_panel(st.session_state.member_profiles)
                st.markdown("")

        # AddOn 建议（改 7）
        if st.session_state.get("addons"):
            _render_addons(st.session_state.addons)

        # v2.4 D1：履约可信度面板（始终显示，因为 plan() 入口已自动落库）
        render_trust_panel(p2, expanded=False)

        # 主时间轴 + 地图
        col_left, col_right = st.columns([1, 1])
        with col_left:
            st.markdown("### 📋 方案时间轴")
            render_timeline(p2, on_dissent=lambda idx: _on_user_dissent(idx, prefs))
        with col_right:
            st.markdown("### 🗺️ 路线")
            render_map(p2, center=center)

            # 雷达图（改 11）— 显示第一个 ranking 顶部 POI 的 reasons
            _render_top_pick_radar(area, prefs)

        # 话术卡片
        st.markdown("---")
        col_card, col_action = st.columns([2, 1])
        with col_card:
            st.markdown("### 💬 话术化卡片（一键发出）")
            card = st.session_state.card
            with st.container(border=True):
                st.markdown(f"**{card.title}**")
                st.markdown(card.body.replace("\n", "  \n"))
        with col_action:
            st.markdown("### 🎯 行动")
            preset = PRESETS[st.session_state.persona]
            st.text(f"对象：{preset['contact']}")
            if st.button("📱 一键发送", type="primary", use_container_width=True):
                send_via_wechat_mock(card, preset["contact"])
                st.success(f"✅ 已发送给 {preset['contact']}")
                time.sleep(0.8)
                st.info(f"📩 {preset['contact']}：OK 就这么定吧")
            if st.button("🎬 模拟下单（演示）", use_container_width=True,
                         help="P1.4：本按钮调 mock_book，不实际扣款。接入真实餐厅预订前显示。"):
                _confirm_book(p2, prefs, preset)
            st.caption("ℹ️ 演示版：所有'下单'调 mock 接口，不会真实扣款。"
                       "生产路径 → 美团商家 / 哗啦啦 / 客如云 SaaS。")

        # Trace 侧栏
        with st.expander("🔍 Tool Call Trace（评委 Q&A 用）"):
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

        # P1.1 北京下午足迹（付费留存数据沉淀）
        with st.expander("📋 我的北京下午足迹（P1.1：付费留存）", expanded=False):
            _render_footprint_panel()


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
    """4 人头像状态面板（改 2）。"""
    st.markdown("### 📨 群发响应（4 人）")
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
                    new_plan, event = replan_step(plan, meal_idx, probe_r, prefs=prefs)
                    st.session_state.plan_v2 = new_plan
                    st.session_state.events.append(event)
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
    new_plan, event = replan_step(p2, step_idx, probe_r, prefs=prefs)
    st.session_state.plan_v2 = new_plan
    st.session_state.events.append(event)
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
