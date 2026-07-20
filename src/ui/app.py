"""BJ-Pal Streamlit Web UI（v2 总集成）。

跑法：
    python3 -m streamlit run src/ui/app.py

v2 新增：
- Hero 区微信对话开场（改 10）
- 缓存/估算路由时间 + 4 模式对比（改 1 + 改 6B）
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

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from html import escape
import json
import sys
from threading import Lock
import time
import uuid
import os
from pathlib import Path

import streamlit as st

SRC_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC_ROOT))

from agents.addon_agent import suggest_addons  # noqa: E402
from agents.group_harmony import group_rank  # noqa: E402
from agents.planner import screen_candidates  # noqa: E402
from agents.preference_mirror import (  # noqa: E402
    detect_has_elderly,
    detect_screening_mode,
)
from agents.replanner import replan_step  # noqa: E402
from agents.skills import describe_skills  # noqa: E402
from agents.types import UserPreferences  # noqa: E402
from agents.user_memory import infer_from_user_input  # noqa: E402
from application import (  # noqa: E402
    PREFERENCE_PROVIDED_FIELDS,
    PlanRequest,
    PlanningClarificationRequired,
    PlanningCallbacks,
    PlanningService,
)
from clarifications import (  # noqa: E402
    ClarificationContinuationService,
    ClarificationExpired,
    ClarificationInProgress,
    ClarificationNotFound,
    ClarificationResolutionConflict,
    InvalidClarificationTransition,
)
from operations import (  # noqa: E402
    DEMO_RECONCILER_ID,
    DEMO_TENANT_ID,
    SideEffectOperationService,
    approve_sandbox_booking,
    build_sandbox_booking_draft,
    execute_next_sandbox_booking,
    request_sandbox_booking,
)
from outcomes import (  # noqa: E402
    FeedbackExpired,
    FeedbackIdempotencyConflict,
    FeedbackNotFound,
    FeedbackPhaseConflict,
    PlanFeedbackService,
    TrialClosed,
    TrialConsentMismatch,
    TrialEnrollmentConflict,
    TrialIntegrityError,
    TrialNotActive,
    TrialNotFound,
    TrialParticipantWithdrawn,
    sha256_json,
)
from tools.amap_search import resolve_area_center, search_pois  # noqa: E402
from tools.availability_probe import user_dissent_probe  # noqa: E402
from tools.footprint import cumulative_stats, fetch_recent_sessions  # noqa: E402
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
from ui.opportunity_panel import render_opportunity_panel  # noqa: E402
from ui.radar import render_radar  # noqa: E402
from ui.timeline import render_timeline  # noqa: E402
from ui.trust_panel import (  # noqa: E402
    render_member_weights_panel,
    render_trust_panel,
)


PRIMARY_WORKSPACE_COLUMNS = ("plan", "map")
SECONDARY_RESULT_TABS = ("发送", "结果反馈", "诊断")
DIAGNOSTIC_LABEL = "诊断"
AGENT_SKILL_PANEL_LABEL = "Agent 能力目录"
TASK_BAR_FIELDS = ("persona", "area", "budget", "start_time", "duration", "mode", "generate")
SIDEBAR_SECTIONS = ("试用", "记忆")
REROUTE_MEMORY_KEY = "reroute_memory_poi_names"
PENDING_CLARIFICATION_KEY = "pending_clarification"
SIDE_EFFECT_OPERATION_KEY = "side_effect_operation_id"
PLAN_FEEDBACK_KEY = "plan_feedback_invitation"
PLAN_FEEDBACK_ERROR_KEY = "plan_feedback_error"
TRIAL_PARTICIPANT_KEY = "trial_participant"
ACTIVE_TRIAL_ENV = "BJ_PAL_ACTIVE_TRIAL_ID"
PRODUCT_PAGE_TITLE = "BJ-Pal · 周末闲时活动规划"
PRODUCT_KICKER = "BJ-Pal · 北京周末闲时规划"
PRODUCT_HEADLINE = "把周末半天，排成一条能出发的路线"
PRODUCT_SUBTITLE = "面向北京本地 3-5 小时闲时出行，自动统筹片区、预算、路线、排队风险和可发送话术。"
DEFAULT_SHOWCASE_QUERY = "今天下午带老婆和 5 岁娃出去玩，别离家太远，4 小时左右。老婆减脂，娃喜欢动物。"
PLAN_STREAM_STEPS = (
    "正在理解你的偏好、片区和时间窗口...",
    "正在读取左侧已确认记忆，只作为约束参考...",
    "正在调用 LLM 生成可执行路线...",
    "正在整理时间轴、路线和可发送文案...",
)
SCREENING_STREAM_STEPS = (
    "正在理解筛选目标...",
    "查询POI候选：餐饮服务",
    "调用排序器：预算、距离、偏好和拥挤风险",
)
PROBE_STREAM_STEPS = (
    "调用排队探针：检查热门地点等待时间",
    "调用天气工具：判断户外路线是否受影响",
    "检查商家状态：营业、预约和拒单风险",
)
REROUTE_STREAM_STEPS = (
    "正在锁定当前不想去的地点...",
    "排除本轮已经出现过的地点...",
    "查询同类替补POI并重排路线...",
)
TRACE_WINDOW_TITLE = "模型执行过程"
TRACE_WINDOW_MAX_LINES = 5
TRACE_WINDOW_HEIGHT_PX = 118
TRACE_WINDOW_TICK_INTERVAL_S = 0.45
TRACE_TOKEN_CHARS_PER_TOKEN = 2
TRACE_TOKEN_PER_SECOND_ESTIMATE = 18
TRACE_STREAM_PREVIEW_CHARS = 280
PLAN_STATUS_LABEL = "正在生成方案"
PLAN_POSTCHECK_LABEL = "正在检查排队、天气和商家状态"
PLAN_STATUS_EXPANDED_WHILE_RUNNING = True
PLAN_STATUS_EXPANDED_AFTER_DONE = False

PRESETS = {
    "family": {
        "label": "家庭出行",
        "user_input": DEFAULT_SHOWCASE_QUERY,
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

PLANNING_SERVICE = PlanningService()
_CLARIFICATION_SERVICE = None
_OPERATION_SERVICE = None
_FEEDBACK_SERVICE = None

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


def estimate_trace_tokens(lines, *, elapsed_s: float = 0.0) -> int:
    """Estimate visible progress tokens until provider usage can be surfaced in UI."""
    chars = sum(len(str(line)) for line in lines)
    text_tokens = max(len(lines), chars // TRACE_TOKEN_CHARS_PER_TOKEN)
    wait_tokens = int(max(elapsed_s, 0.0) * TRACE_TOKEN_PER_SECOND_ESTIMATE)
    return max(1, text_tokens + wait_tokens)


def build_trace_window_html(
    lines,
    *,
    token_count: int,
    title: str = TRACE_WINDOW_TITLE,
    max_lines: int = TRACE_WINDOW_MAX_LINES,
    stream_text: str = "",
) -> str:
    """Build a fixed-height trace window that only shows the latest progress lines."""
    all_lines = [str(line) for line in lines if str(line).strip()]
    visible = all_lines[-max_lines:]
    rows = "\n".join(
        f"<div class='bjpal-trace-line'>{escape(line)}</div>"
        for line in visible
    )
    stream_preview = str(stream_text or "")[-TRACE_STREAM_PREVIEW_CHARS:]
    stream_block = ""
    if stream_preview:
        stream_block = (
            "<div class='bjpal-trace-stream'>"
            "<span>模型输出</span>"
            f"<code>{escape(stream_preview)}</code>"
            "</div>"
        )
    hidden_count = max(0, len(all_lines) - len(visible))
    hidden_label = (
        f"<span class='bjpal-trace-hidden'>已滑过 {hidden_count} 行</span>"
        if hidden_count
        else "<span class='bjpal-trace-hidden'>实时更新</span>"
    )
    return (
        "<div class='bjpal-trace-window'>"
        "<div class='bjpal-trace-meta'>"
        f"<span>{escape(title)}</span>"
        f"<span>token 估算 {int(token_count)}</span>"
        "</div>"
        f"<div class='bjpal-trace-lines'>{rows}</div>"
        f"{stream_block}"
        f"{hidden_label}"
        "</div>"
    )


def upsert_trace_waiting_line(lines: list[str], waiting_idx: int | None, *, elapsed_s: float) -> int:
    """Insert or update the single waiting line so long calls do not repeat templates."""
    message = f"等待模型或工具返回中 {elapsed_s:.1f}s"
    if waiting_idx is None or waiting_idx >= len(lines):
        lines.append(message)
        return len(lines) - 1
    lines[waiting_idx] = message
    return waiting_idx


def extract_model_status_events(stream_text: str) -> list[str]:
    """Extract completed JSONL status events from model stream text."""
    events: list[str] = []
    for raw_line in str(stream_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("event") != "status":
            continue
        text = str(event.get("text") or "").strip()
        if text:
            events.append(f"模型：{text}")
    return events


def _render_trace_window(
    placeholder,
    lines,
    *,
    started_at: float,
    title: str,
    stream_text: str = "",
) -> None:
    token_count = estimate_trace_tokens(
        [*lines, stream_text],
        elapsed_s=time.time() - started_at,
    )
    placeholder.markdown(
        build_trace_window_html(
            lines,
            token_count=token_count,
            title=title,
            stream_text=stream_text,
        ),
        unsafe_allow_html=True,
    )


def _run_with_progress_trace(
    steps: tuple[str, ...],
    func,
    *,
    title: str = TRACE_WINDOW_TITLE,
    accepts_stream_callbacks: bool = False,
):
    """Run a blocking operation while keeping a fixed-height progress trace alive."""
    placeholder = st.empty()
    started_at = time.time()
    lock = Lock()
    lines: list[str] = []
    stream_parts: list[str] = []
    for line in steps:
        lines.append(line)
        _render_trace_window(placeholder, lines, started_at=started_at, title=title)
        time.sleep(0.06)

    def on_progress(message: str) -> None:
        with lock:
            lines.append(str(message))

    def on_token(token: str) -> None:
        if not token:
            return
        with lock:
            stream_parts.append(str(token))

    def on_stream_event(message: str) -> None:
        if not message:
            return
        with lock:
            lines.append(str(message))

    with ThreadPoolExecutor(max_workers=1) as executor:
        if accepts_stream_callbacks:
            future = executor.submit(func, on_token, on_progress, on_stream_event)
        else:
            future = executor.submit(func)
        waiting_idx: int | None = None
        model_status_count = 0
        while True:
            try:
                result = future.result(timeout=TRACE_WINDOW_TICK_INTERVAL_S)
            except TimeoutError:
                elapsed = time.time() - started_at
                with lock:
                    stream_text = "".join(stream_parts)
                    status_events = extract_model_status_events(stream_text)
                    new_status_events = status_events[model_status_count:]
                    if new_status_events:
                        if waiting_idx is not None and waiting_idx < len(lines):
                            lines.pop(waiting_idx)
                            waiting_idx = None
                        lines.extend(new_status_events)
                        model_status_count = len(status_events)
                    waiting_idx = upsert_trace_waiting_line(
                        lines,
                        waiting_idx,
                        elapsed_s=elapsed,
                    )
                    current_lines = list(lines)
                _render_trace_window(
                    placeholder,
                    current_lines,
                    started_at=started_at,
                    title=title,
                    stream_text=stream_text,
                )
                continue
            except Exception:
                with lock:
                    stream_text = "".join(stream_parts)
                    status_events = extract_model_status_events(stream_text)
                    new_status_events = status_events[model_status_count:]
                    if new_status_events:
                        if waiting_idx is not None and waiting_idx < len(lines):
                            lines.pop(waiting_idx)
                            waiting_idx = None
                        lines.extend(new_status_events)
                        model_status_count = len(status_events)
                    lines.append("调用异常，正在展示错误信息...")
                    current_lines = list(lines)
                _render_trace_window(
                    placeholder,
                    current_lines,
                    started_at=started_at,
                    title=title,
                    stream_text=stream_text,
                )
                raise
            with lock:
                stream_text = "".join(stream_parts)
                status_events = extract_model_status_events(stream_text)
                new_status_events = status_events[model_status_count:]
                if new_status_events:
                    if waiting_idx is not None and waiting_idx < len(lines):
                        lines.pop(waiting_idx)
                        waiting_idx = None
                    lines.extend(new_status_events)
                    model_status_count = len(status_events)
                lines.append("完成，正在渲染结果...")
                current_lines = list(lines)
            _render_trace_window(
                placeholder,
                current_lines,
                started_at=started_at,
                title=title,
                stream_text=stream_text,
            )
            return result


def remember_manual_preference(user_id: str, raw: str, *, client=None):
    """Use LLM intake to persist manually entered preferences for the current user."""
    if not user_id or not raw or not raw.strip():
        return []
    return infer_from_user_input(
        user_id,
        raw.strip(),
        client=client,
        use_llm=True,
    )


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


def format_data_profile_notice(profile) -> str:
    """Return an honest, compact data notice for the delivery layer."""
    if profile.contains_synthetic_data:
        return (
            f"数据模式：{profile.name} / {profile.classification}。"
            "当前结果用于公开可复现演示，不代表实时热度、余位或预订成功。"
        )
    return f"数据模式：{profile.name} / {profile.classification}。"


def _clarification_service() -> ClarificationContinuationService:
    """Lazily open the local continuation store only when a request needs it."""
    global _CLARIFICATION_SERVICE
    if _CLARIFICATION_SERVICE is None:
        _CLARIFICATION_SERVICE = ClarificationContinuationService()
    return _CLARIFICATION_SERVICE


def _operation_service() -> SideEffectOperationService:
    """Lazily open the local sandbox operation store for the product rehearsal."""
    global _OPERATION_SERVICE
    if _OPERATION_SERVICE is None:
        _OPERATION_SERVICE = SideEffectOperationService()
    return _OPERATION_SERVICE


def _feedback_service() -> PlanFeedbackService:
    """Lazily open the append-only feedback evidence store."""
    global _FEEDBACK_SERVICE
    if _FEEDBACK_SERVICE is None:
        _FEEDBACK_SERVICE = PlanFeedbackService()
    return _FEEDBACK_SERVICE


def _active_trial_participant_capability() -> str | None:
    participant = st.session_state.get(TRIAL_PARTICIPANT_KEY)
    if not isinstance(participant, dict):
        return None
    capability = participant.get("capability")
    return capability if isinstance(capability, str) else None


def _render_trial_enrollment_panel() -> None:
    """Opt into an operator-created trial without persisting raw capabilities."""
    trial_id = os.environ.get(ACTIVE_TRIAL_ENV, "").strip()
    if not trial_id:
        return
    st.markdown("## 知情试用")
    try:
        trial = _feedback_service().get_trial(trial_id)
    except (TrialNotFound, TrialIntegrityError, OSError, RuntimeError, ValueError):
        st.error("当前试用批次不可用；普通演示仍可继续。")
        return
    notice = trial.consent_notice
    st.caption(
        "自愿参与；只记录随机参与者/方案标识、枚举反馈和时间，不收姓名、联系方式或自由文本。"
    )
    st.caption(
        f"反馈截止：{notice['ends_at']} · 原始记录保留提示：{notice['retention_until']}"
    )
    st.caption(
        "每个阶段至少达到批次门槛才展示比率；不同参与凭证不等于已验证的不同真人。"
    )
    participant = st.session_state.get(TRIAL_PARTICIPANT_KEY)
    if isinstance(participant, dict) and participant.get("trial_id") == trial_id:
        st.success("本会话已完成知情加入；参与凭证只保存在当前会话。")
        st.caption(
            f"participant {str(participant.get('participant_id', ''))[-8:]} · "
            f"notice {trial.consent_notice_sha256[:12]}…"
        )
        if st.button(
            "退出本次试用",
            key=f"withdraw-trial-{trial_id}",
            use_container_width=True,
        ):
            try:
                _feedback_service().withdraw_trial(
                    trial_id=trial_id,
                    participant_capability=participant["capability"],
                )
            except (TrialClosed, TrialNotActive):
                st.warning("试用已经结束，冻结证据不会再改变。")
                return
            except (TrialNotFound, TrialIntegrityError, OSError, RuntimeError, ValueError):
                st.error("暂时无法记录退出；没有把状态伪装成已退出。")
                return
            st.session_state.pop(TRIAL_PARTICIPANT_KEY, None)
            st.session_state.pop(PLAN_FEEDBACK_KEY, None)
            st.success("已退出：后续不能新增反馈，未冻结汇总会排除本参与者。")
            st.rerun()
        return
    if trial.status == "closed":
        st.info("本批次已经冻结，不再接受新参与者。")
        return
    with st.form(key=f"trial-enroll-{trial_id}"):
        enrollment_capability = st.text_input(
            "一次性加入码",
            type="password",
            help="由试用组织者单独发放；系统只在数据库保存其 SHA-256。",
        )
        consent_attested = st.checkbox(
            "我已阅读上述用途、收集字段、退出和保留说明，并自愿参与"
        )
        submitted = st.form_submit_button(
            "加入知情试用",
            type="primary",
            use_container_width=True,
        )
    if not submitted:
        return
    try:
        enrolled = _feedback_service().enroll_trial(
            trial_id=trial_id,
            enrollment_capability=enrollment_capability.strip(),
            consent_notice_sha256=trial.consent_notice_sha256,
            consent_attested=consent_attested,
        )
    except TrialConsentMismatch:
        st.warning("必须明确同意当前这版说明，不能沿用旧版同意。")
        return
    except TrialEnrollmentConflict:
        st.warning("这个一次性加入码已经使用过。")
        return
    except (TrialNotFound, TrialNotActive, TrialClosed):
        st.warning("加入码无效、已过期，或试用批次已结束。")
        return
    except (TrialIntegrityError, OSError, RuntimeError, ValueError):
        st.error("暂时无法加入试用；加入码和同意状态没有被伪装成已保存。")
        return
    st.session_state[TRIAL_PARTICIPANT_KEY] = enrolled.to_public_dict()
    st.success("已加入；下一份新生成方案会绑定到本批次。")
    st.rerun()


def _issue_ui_feedback_invitation(plan) -> None:
    """Bind a session-only capability to the exact plan revision shown in UI."""
    profile = st.session_state.get("data_profile")
    st.session_state.pop(PLAN_FEEDBACK_KEY, None)
    st.session_state.pop(PLAN_FEEDBACK_ERROR_KEY, None)
    if profile is None:
        st.session_state[PLAN_FEEDBACK_ERROR_KEY] = "本次方案缺少数据口径，无法收集结果证据。"
        return
    try:
        invitation = _feedback_service().issue(
            plan_id=plan.plan_id,
            plan_artifact_sha256=sha256_json(plan.to_dict()),
            data_profile_name=profile.name,
            data_profile_classification=profile.classification,
            trial_participant_capability=_active_trial_participant_capability(),
        )
    except (TrialNotFound, TrialNotActive, TrialClosed, TrialParticipantWithdrawn):
        st.session_state.pop(TRIAL_PARTICIPANT_KEY, None)
        st.session_state[PLAN_FEEDBACK_ERROR_KEY] = (
            "试用参与凭证无效、已退出或批次已结束；本方案未计入试用证据。"
        )
        return
    except Exception:
        st.session_state[PLAN_FEEDBACK_ERROR_KEY] = "结果证据存储暂时不可用。"
        return
    st.session_state[PLAN_FEEDBACK_KEY] = {
        "plan_id": plan.plan_id,
        **invitation.to_public_dict(
            feedback_url=f"/v1/plans/{plan.plan_id}/feedback"
        ),
    }


def _store_planning_result(result):
    """Commit one canonical result to UI state for initial and continued plans."""
    p2 = result.final_plan
    events = list(result.reroute_events)
    prefs = result.request.preferences
    area = result.request.area_anchor
    st.session_state.plan_v1 = result.initial_plan
    st.session_state.prefs = prefs
    st.session_state.area = area
    st.session_state.plan_v2 = p2
    st.session_state.events = events
    st.session_state.data_profile = result.data_profile
    st.session_state.requirements = result.requirements
    st.session_state.constraints = result.constraints
    st.session_state.pop(SIDE_EFFECT_OPERATION_KEY, None)
    _issue_ui_feedback_invitation(p2)
    _seed_reroute_memory(p2, events)
    st.session_state.addons = suggest_addons(p2, prefs)
    preset = PRESETS.get(result.request.persona, PRESETS[st.session_state.persona])
    card_style = (
        "elderly_friendly"
        if detect_has_elderly(result.request.user_input)
        else "default"
    )
    st.session_state.card = render_im_card(
        p2,
        audience=preset["audience"],
        style=card_style,
    )
    return p2, events, card_style


def _render_clarification_continuation() -> None:
    """Render and execute one persisted clarification decision."""
    pending = st.session_state.get(PENDING_CLARIFICATION_KEY)
    if not pending:
        return
    continuation_id = str(pending.get("continuation_id") or "")
    try:
        session = _clarification_service().get(continuation_id)
    except Exception:
        st.error("澄清会话暂时无法读取，请重新生成方案。")
        return
    if session is None:
        st.error("澄清会话不存在，请重新生成方案。")
        st.session_state.pop(PENDING_CLARIFICATION_KEY, None)
        return
    if session.status == "expired":
        st.warning("这次澄清已过期，请重新生成方案。")
        st.session_state.pop(PENDING_CLARIFICATION_KEY, None)
        return

    question = (session.decision_payload.get("questions") or [{}])[0]
    options = {item.option_id: item for item in session.options}
    with st.container(border=True):
        st.markdown("### 生成前还需要你确认一项")
        st.write(question.get("prompt") or "请选择本次应采用的约束。")
        st.caption(
            f"decision {session.decision_sha256[:12]}… · "
            f"有效至 {session.expires_at}"
        )
        with st.form(key=f"clarification-{continuation_id}"):
            option_id = st.radio(
                "选择处理方式",
                options=list(options),
                format_func=lambda value: options[value].label,
            )
            answer = st.text_input(
                "补充内容",
                placeholder="选择“补充”或“重新描述”时填写",
            )
            submitted = st.form_submit_button(
                "确认并继续生成",
                type="primary",
                use_container_width=True,
            )
        if not submitted:
            return
        option = options[option_id]
        normalized_answer = answer.strip() if option.requires_answer else None
        if option.requires_answer and not normalized_answer:
            st.warning("该选项需要补充具体内容。")
            return

        owner = f"ui-{uuid.uuid4().hex}"
        claimed = False
        try:
            _, resolved_request = _clarification_service().resolve_request(
                continuation_id=continuation_id,
                delivery="sync",
                option_id=option_id,
                answer=normalized_answer,
            )
            session = _clarification_service().claim_execution(
                continuation_id=continuation_id,
                owner=owner,
            )
            claimed = session.status == "executing"
            if session.status == "completed":
                st.session_state.pop(PENDING_CLARIFICATION_KEY, None)
                st.info("该澄清已经执行完成。")
                return
            with st.status("已确认，继续生成方案", expanded=False):
                result = PLANNING_SERVICE.execute(resolved_request)
            _clarification_service().complete(
                continuation_id=continuation_id,
                owner=owner,
                result_payload=result.to_dict(),
            )
            _store_planning_result(result)
            st.session_state.pop(PENDING_CLARIFICATION_KEY, None)
            st.rerun()
        except PlanningClarificationRequired as exc:
            try:
                next_session = _clarification_service().issue(
                    request=resolved_request,
                    error=exc,
                    delivery="sync",
                )
                _clarification_service().complete(
                    continuation_id=continuation_id,
                    owner=owner,
                    result_payload={
                        "next_clarification": {
                            "requirements": exc.decision.to_dict(),
                            "continuation": next_session.to_public_dict(),
                        }
                    },
                )
                st.session_state[PENDING_CLARIFICATION_KEY] = (
                    next_session.to_public_dict()
                )
                st.rerun()
            except Exception:
                if claimed:
                    _clarification_service().release_execution(
                        continuation_id=continuation_id,
                        owner=owner,
                    )
                st.error("下一项澄清无法保存，请稍后重试。")
        except (
            ClarificationExpired,
            ClarificationInProgress,
            ClarificationNotFound,
            ClarificationResolutionConflict,
            InvalidClarificationTransition,
            ValueError,
        ) as exc:
            if claimed:
                _clarification_service().release_execution(
                    continuation_id=continuation_id,
                    owner=owner,
                )
            st.warning(f"无法继续这次澄清：{exc}")
        except Exception:
            if claimed:
                _clarification_service().release_execution(
                    continuation_id=continuation_id,
                    owner=owner,
                )
            st.error("继续生成失败；已保留本次答案，可以安全重试。")


def _render_manual_memory_input(user_id: str) -> None:
    """Render the simple right-side memory capture control."""
    with st.container(border=True):
        st.markdown("### 记住我的偏好")
        st.caption("写下口味、忌口、过敏、偏爱的环境或明确不想要的安排，LLM 会抽取并保存到左侧记忆。")
        feedback = st.session_state.pop("manual_memory_feedback", None)
        if feedback:
            st.success(feedback)
        raw = st.text_area(
            "偏好/禁忌",
            value=st.session_state.get("manual_memory_input", ""),
            height=72,
            placeholder="比如：我乳糖不耐受，喜欢酸口，别安排自助餐；爸妈不吃辣，想坐安静一点。",
            key="manual_memory_input",
        )
        col_save, col_clear = st.columns([1, 1])
        with col_save:
            if st.button("交给 LLM 记住", type="primary", key="manual_memory_save"):
                entries = remember_manual_preference(user_id, raw)
                if entries:
                    st.session_state["manual_memory_feedback"] = f"已沉淀 {len(entries)} 条记忆，左侧面板已更新。"
                    st.rerun()
                else:
                    st.info("没有抽取到可沉淀的偏好或禁忌。")
        with col_clear:
            if st.button("清空输入", key="manual_memory_clear"):
                st.session_state["manual_memory_input"] = ""
                st.rerun()


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
    current_user_id = st.session_state.user_id

    with st.sidebar:
        _render_trial_enrollment_panel()
        if os.environ.get(ACTIVE_TRIAL_ENV, "").strip():
            st.divider()
        st.markdown("## 记忆")
        st.caption("当前用户画像会影响后续方案生成。")
        new_uid = st.text_input(
            "当前用户",
            value=st.session_state.user_id,
            help="切换用户后会展示对应的偏好和禁忌记忆。",
        )
        if new_uid != st.session_state.user_id:
            st.session_state.user_id = new_uid
            st.rerun()
        current_user_id = st.session_state.user_id
        render_memory_panel(current_user_id)

    _render_product_header()

    user_input = st.text_area(
        "这次想怎么安排",
        value=st.session_state.get("user_input", PRESETS[st.session_state.get("persona", "family")]["user_input"]),
        height=96,
        placeholder="比如：今天下午带家人逛一逛，别太远，想吃清淡一点。",
    )
    _render_manual_memory_input(current_user_id)

    auto_mode = "screening" if detect_screening_mode(user_input) else "planning"
    persona_key, preset, area, budget, target_start, duration_hours, mode_choice, gen_btn = _render_task_bar(
        auto_mode=auto_mode,
    )
    st.session_state["mode"] = mode_choice

    if gen_btn:
        st.session_state.pop(PENDING_CLARIFICATION_KEY, None)
        augmented_input = user_input
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
                result = _run_with_progress_trace(
                    SCREENING_STREAM_STEPS,
                    lambda: screen_candidates(
                        user_input=augmented_input, persona=persona_key,
                        prefs=prefs, area_anchor=area,
                        category="food", top_k=8,
                    ),
                )
                st.session_state.screening_result = result
                status.update(
                    label=f"已筛出 {len(result.get('candidates', []))} 个候选",
                    state="complete",
                )
        else:
            st.session_state.pop("screening_result", None)

            with st.status(PLAN_STATUS_LABEL, expanded=PLAN_STATUS_EXPANDED_WHILE_RUNNING) as status:
                t0 = time.time()
                request = PlanRequest(
                    user_input=augmented_input,
                    persona=persona_key,
                    preferences=prefs,
                    area_anchor=area,
                    user_id=current_user_id,
                    provided_fields=frozenset(
                        {
                            "user_input",
                            "persona",
                            "preferences",
                            "area_anchor",
                            "user_id",
                        }
                    ) | PREFERENCE_PROVIDED_FIELDS,
                )

                def _on_initial_plan(p1):
                    st.session_state.plan_v1 = p1
                    status.update(
                        label=f"初版方案完成，{len(p1.steps)} 步，{time.time()-t0:.1f}s",
                        state="running",
                        expanded=PLAN_STATUS_EXPANDED_WHILE_RUNNING,
                    )

                try:
                    result = _run_with_progress_trace(
                        PLAN_STREAM_STEPS + PROBE_STREAM_STEPS,
                        lambda on_token, on_progress, on_stream_event: PLANNING_SERVICE.execute(
                            request,
                            callbacks=PlanningCallbacks(
                                on_token=on_token,
                                on_progress=on_progress,
                                on_stream_event=on_stream_event,
                                on_initial_plan=_on_initial_plan,
                            ),
                        ),
                        accepts_stream_callbacks=True,
                    )
                except PlanningClarificationRequired as exc:
                    try:
                        session = _clarification_service().issue(
                            request=request,
                            error=exc,
                            delivery="sync",
                        )
                    except Exception:
                        status.update(
                            label="澄清会话保存失败",
                            state="error",
                            expanded=True,
                        )
                        st.error("生成前需要补充信息，但本次澄清无法持久化。")
                        return
                    st.session_state[PENDING_CLARIFICATION_KEY] = (
                        session.to_public_dict()
                    )
                    status.update(
                        label="生成前需要补充一项信息",
                        state="complete",
                        expanded=False,
                    )
                    st.rerun()
                except Exception as e:
                    status.update(label=f"生成失败：{e}", state="error", expanded=True)
                    st.exception(e)
                    return

                status.update(label=PLAN_POSTCHECK_LABEL, state="running",
                              expanded=PLAN_STATUS_EXPANDED_WHILE_RUNNING)
                p2, events, card_style = _store_planning_result(result)
                if events:
                    status.update(
                        label=f"方案已生成，已自动调整 {len(events)} 处",
                        state="complete",
                        expanded=PLAN_STATUS_EXPANDED_AFTER_DONE,
                    )
                else:
                    status.update(
                        label="方案已生成，无需调整",
                        state="complete",
                        expanded=PLAN_STATUS_EXPANDED_AFTER_DONE,
                    )

            if card_style == "elderly_friendly":
                st.info("已切换到大字号简化卡片，便于老人阅读。")

    _render_clarification_continuation()

    if "screening_result" in st.session_state and st.session_state.get("mode") == "screening":
        _render_screening(st.session_state.screening_result)
        return

    if "plan_v2" in st.session_state:
        p2 = st.session_state.plan_v2
        prefs = st.session_state.prefs
        area = st.session_state.area
        center = resolve_area_center(area)

        if st.session_state.get("data_profile") is not None:
            st.caption(format_data_profile_notice(st.session_state.data_profile))
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
            _render_feedback_panel(p2)
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
            align-items: flex-start;
            flex-direction: column;
            gap: 0.46rem;
            border-bottom: 1px solid var(--bjpal-line);
            padding: 0.25rem 0.05rem 1.05rem;
            margin-bottom: 1.05rem;
          }
          .bjpal-topline > div {
            min-width: 0;
          }
          .bjpal-kicker {
            color: var(--bjpal-accent);
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0;
            margin-bottom: 0.22rem;
          }
          .bjpal-title {
            color: var(--bjpal-ink);
            font-size: clamp(1.52rem, 2.5vw, 2.18rem);
            line-height: 1.08;
            font-weight: 760;
            letter-spacing: 0;
            margin: 0;
            white-space: nowrap;
          }
          .bjpal-subtitle {
            color: var(--bjpal-muted);
            font-size: 0.92rem;
            line-height: 1.55;
            max-width: 560px;
            margin: 0;
            text-align: left;
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
            white-space: nowrap;
          }
          .stButton > button p {
            white-space: nowrap;
            word-break: keep-all;
            overflow-wrap: normal;
          }
          .bjpal-trace-window {
            height: 118px;
            overflow: hidden;
            border: 1px solid var(--bjpal-line);
            border-radius: 8px;
            background: rgba(249,250,251,0.74);
            color: #6b7280;
            padding: 0.56rem 0.68rem;
            margin: 0.25rem 0 0.45rem;
            font-size: 0.76rem;
            line-height: 1.42;
          }
          .bjpal-trace-meta {
            display: flex;
            gap: 0.75rem;
            color: #6b7280;
            font-weight: 650;
            white-space: nowrap;
          }
          .bjpal-trace-meta span:last-child {
            margin-left: auto;
          }
          .bjpal-trace-lines {
            margin-top: 0.34rem;
          }
          .bjpal-trace-line {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .bjpal-trace-stream {
            margin-top: 0.34rem;
            border-top: 1px solid rgba(107,114,128,0.18);
            padding-top: 0.3rem;
          }
          .bjpal-trace-stream span {
            display: block;
            color: #9ca3af;
            font-size: 0.69rem;
            margin-bottom: 0.12rem;
          }
          .bjpal-trace-stream code {
            display: block;
            color: #6b7280;
            background: transparent;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            font-size: 0.71rem;
          }
          .bjpal-trace-hidden {
            display: block;
            margin-top: 0.24rem;
            color: #9ca3af;
            font-size: 0.69rem;
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
              gap: 0.42rem;
            }
            .bjpal-title {
              font-size: clamp(0.98rem, 4.9vw, 1.42rem);
            }
            .bjpal-subtitle {
              max-width: 100%;
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
    cols = st.columns(3)
    cols[0].metric("停靠点", snap["stop_count"])
    cols[1].metric("路上", snap["travel_label"])
    cols[2].metric("调整", snap["reroute_count"])


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
        if not st.session_state.get(SIDE_EFFECT_OPERATION_KEY):
            if st.button("创建沙箱预订请求", use_container_width=True):
                _request_sandbox_operation(plan, prefs)
                st.rerun()

    _render_sandbox_operation_panel()

    if st.session_state.persona == "friends":
        st.markdown("### 群内确认")
        if st.button("发到群里征求意见", use_container_width=True):
            _handle_group_broadcast(plan)
        if "broadcast_responses" in st.session_state:
            _render_broadcast_panel(st.session_state.broadcast_responses, plan, prefs)
            if st.session_state.get("member_profiles"):
                render_member_weights_panel(st.session_state.member_profiles)

    st.caption(
        "当前是演示环境：消息发送仍为 mock；预订必须经过精确报价、独立审批、"
        "沙箱 worker 和可校验回执，不会触发真实扣款或商家请求。"
    )


FEEDBACK_REASON_LABELS = {
    "too_expensive": "预算太高",
    "too_far": "距离太远",
    "schedule_unrealistic": "时间安排不现实",
    "unsuitable_poi": "地点不合适",
    "route_issue": "路线有问题",
    "weather_issue": "天气影响",
    "availability_issue": "营业或余位问题",
    "group_disagreement": "同行人意见不一致",
    "other": "其他（不收集自由文本）",
}
DECISION_FEEDBACK_LABELS = {
    "accepted": "接受这版方案",
    "requested_change": "需要调整后再决定",
    "rejected": "不采用这版方案",
}
OUTCOME_FEEDBACK_LABELS = {
    "completed": "按这版方案完成",
    "partially_completed": "只完成了一部分",
    "abandoned": "最终没有执行",
}


def _feedback_idempotency_key(plan_id: str, artifact_sha256: str, phase: str) -> str:
    evidence = {
        "session_id": st.session_state.session_id,
        "plan_id": plan_id,
        "plan_artifact_sha256": artifact_sha256,
        "phase": phase,
    }
    return f"ui-feedback-{sha256_json(evidence)[:32]}"


def _submit_ui_feedback(
    *,
    invitation: dict,
    phase: str,
    value: str,
    reason_codes: list[str],
) -> None:
    try:
        _feedback_service().submit(
            plan_id=invitation["plan_id"],
            capability=invitation["capability"],
            idempotency_key=_feedback_idempotency_key(
                invitation["plan_id"],
                invitation["plan_artifact_sha256"],
                phase,
            ),
            phase=phase,
            value=value,
            reason_codes=tuple(reason_codes),
        )
    except FeedbackExpired:
        st.warning("这次反馈凭证已过期，请重新生成方案后再记录。")
        return
    except FeedbackNotFound:
        st.warning("反馈凭证与当前方案不匹配，请重新生成方案。")
        return
    except FeedbackIdempotencyConflict:
        st.error("同一次提交被改成了不同内容；系统已拒绝覆盖原记录。")
        return
    except FeedbackPhaseConflict:
        st.info("这个方案版本已经记录过该阶段，追加写入存储不会覆盖原记录。")
        return
    except TrialParticipantWithdrawn:
        st.warning("你已经退出这次试用，系统不会继续加入新证据。")
        return
    except (TrialClosed, TrialNotActive):
        st.warning("试用批次已经结束；冻结证据不会再改变。")
        return
    except TrialIntegrityError:
        st.error("试用证据链校验失败；本次选择没有被写入。")
        return
    except (OSError, RuntimeError, ValueError):
        st.error("结果证据暂时无法保存；没有把本次选择伪装成已记录。")
        return
    st.success("已记录为用户自报、未经独立核验的结果证据。")
    st.rerun()


def _render_feedback_panel(plan) -> None:
    st.markdown("### 这版方案后来怎么样")
    st.caption(
        "分两次记录：先记是否采纳，实际出行后再记是否完成。"
        "只收集枚举原因，不收姓名、联系方式或自由文本；证据分类为用户自报、未经核验。"
    )
    error = st.session_state.get(PLAN_FEEDBACK_ERROR_KEY)
    if error:
        st.error(error)
        return
    invitation = st.session_state.get(PLAN_FEEDBACK_KEY)
    if not invitation or invitation.get("plan_id") != plan.plan_id:
        st.info("当前方案还没有可用的结果反馈凭证。")
        return
    try:
        reports = _feedback_service().list_reports(
            plan_id=plan.plan_id,
            capability=invitation["capability"],
        )
        summary = (
            _feedback_service().trial_summary(trial_id=invitation["trial_id"])
            if invitation.get("trial_id")
            else _feedback_service().public_summary()
        )
    except FeedbackExpired:
        st.warning("反馈凭证已过期；重新生成方案会创建新的限时凭证。")
        return
    except (FeedbackNotFound, TrialIntegrityError, OSError, RuntimeError, ValueError):
        st.error("暂时无法读取结果证据。")
        return

    reports_by_phase = {report.phase: report for report in reports}
    decision_report = reports_by_phase.get("decision")
    outcome_report = reports_by_phase.get("outcome")

    if decision_report is None:
        with st.form(
            key=f"feedback-decision-{invitation['plan_artifact_sha256'][:12]}"
        ):
            decision = st.radio(
                "你会采用这版安排吗",
                options=list(DECISION_FEEDBACK_LABELS),
                format_func=DECISION_FEEDBACK_LABELS.__getitem__,
            )
            decision_reasons = []
            if decision != "accepted":
                decision_reasons = st.multiselect(
                    "主要原因",
                    options=list(FEEDBACK_REASON_LABELS),
                    format_func=FEEDBACK_REASON_LABELS.__getitem__,
                )
            decision_submitted = st.form_submit_button(
                "记录采纳决定", type="primary", use_container_width=True
            )
        if decision_submitted:
            if decision != "accepted" and not decision_reasons:
                st.warning("请选择至少一个原因。")
            else:
                _submit_ui_feedback(
                    invitation=invitation,
                    phase="decision",
                    value=decision,
                    reason_codes=decision_reasons,
                )
    else:
        st.success(
            f"采纳阶段已记录：{DECISION_FEEDBACK_LABELS[decision_report.value]}"
        )

    if decision_report is not None and outcome_report is None:
        with st.form(
            key=f"feedback-outcome-{invitation['plan_artifact_sha256'][:12]}"
        ):
            outcome = st.radio(
                "实际出行结果",
                options=list(OUTCOME_FEEDBACK_LABELS),
                format_func=OUTCOME_FEEDBACK_LABELS.__getitem__,
            )
            outcome_reasons = []
            if outcome != "completed":
                outcome_reasons = st.multiselect(
                    "未完全完成的主要原因",
                    options=list(FEEDBACK_REASON_LABELS),
                    format_func=FEEDBACK_REASON_LABELS.__getitem__,
                )
            outcome_submitted = st.form_submit_button(
                "记录实际结果", type="primary", use_container_width=True
            )
        if outcome_submitted:
            if outcome != "completed" and not outcome_reasons:
                st.warning("请选择至少一个原因。")
            else:
                _submit_ui_feedback(
                    invitation=invitation,
                    phase="outcome",
                    value=outcome,
                    reason_codes=outcome_reasons,
                )
    elif outcome_report is not None:
        st.success(
            f"结果阶段已记录：{OUTCOME_FEEDBACK_LABELS[outcome_report.value]}"
        )

    is_trial = bool(invitation.get("trial_id"))
    phase_counts = summary[
        "phase_participant_counts" if is_trial else "phase_counts"
    ]
    minimum = summary[
        "minimum_participants" if is_trial else "minimum_phase_samples"
    ]
    st.divider()
    st.caption(
        f"公开汇总门槛：每个阶段至少 {minimum} 份。"
        f"当前采纳阶段 {phase_counts['decision']} 份，结果阶段 {phase_counts['outcome']} 份。"
    )
    if is_trial:
        st.caption(
            "这里按未退出的不同参与凭证计数；它不等于经过身份核验的不同真人。"
        )
    if summary["decision_acceptance_rate"] is None:
        st.caption("采纳率样本不足，暂不展示。")
    else:
        st.metric("自报采纳率", f"{summary['decision_acceptance_rate']:.1%}")
    if summary["outcome_completion_rate"] is None:
        st.caption("完成率样本不足，暂不展示。")
    else:
        st.metric("自报完成率", f"{summary['outcome_completion_rate']:.1%}")


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

    ledger = st.session_state.get("constraints")
    if ledger is not None:
        with st.expander("Constraint Ledger", expanded=False):
            st.caption("自然语言约束、结构化控件值与最终生效值的可追溯记录。")
            st.code(ledger.rewritten_query, language=None)
            rows = [
                {
                    "field": entry.field,
                    "value": entry.value,
                    "source": entry.source,
                    "outcome": entry.outcome,
                    "evidence": entry.evidence,
                }
                for entry in ledger.entries
                if entry.outcome != "default"
            ]
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.caption("本次没有识别到额外文本约束。")

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
                    _issue_ui_feedback_invitation(new_plan)
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
    with st.status("正在换一个", expanded=True) as status:
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
        new_plan, event = _run_with_progress_trace(
            REROUTE_STREAM_STEPS,
            lambda: replan_step(
                p2,
                step_idx,
                probe_r,
                prefs=prefs,
                excluded_poi_names=excluded_names,
            ),
        )
        status.update(label="已完成替换检查", state="complete")
    st.session_state.plan_v2 = new_plan
    st.session_state.events.append(event)
    _issue_ui_feedback_invitation(new_plan)
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


def _request_sandbox_operation(plan, prefs) -> None:
    """Create a quote-bound request without implicitly approving or executing it."""
    meal_steps = [s for s in plan.steps if s.kind == "meal" and s.poi_id]
    if not meal_steps:
        st.warning("方案里没有可申请预订的餐饮步骤。")
        return
    meal = meal_steps[0]
    draft = build_sandbox_booking_draft(
        session_id=st.session_state.session_id,
        poi_id=meal.poi_id,
        poi_name=meal.poi_name,
        target_time=meal.start_time,
        party_size=prefs.party_size,
        amount_minor=prefs.budget_per_person * prefs.party_size * 100,
    )
    operation = request_sandbox_booking(_operation_service(), draft)
    st.session_state[SIDE_EFFECT_OPERATION_KEY] = operation.operation_id


def _render_sandbox_operation_panel() -> None:
    """Render each safety transition as a separate, user-visible action."""
    operation_id = st.session_state.get(SIDE_EFFECT_OPERATION_KEY)
    if not operation_id:
        return
    operation = _operation_service().get(
        operation_id,
        tenant_id=DEMO_TENANT_ID,
    )
    if operation is None:
        st.session_state.pop(SIDE_EFFECT_OPERATION_KEY, None)
        st.warning("沙箱预订请求不存在，请重新创建。")
        return

    with st.container(border=True):
        st.markdown("### 沙箱预订操作")
        st.caption(
            f"operation `{operation.operation_id}` · status `{operation.status}` · "
            "sandbox `true`"
        )
        st.code(
            "\n".join(
                (
                    f"餐厅：{operation.action_payload['poi_name']}",
                    f"时间：{operation.action_payload['target_time']}",
                    f"人数：{operation.action_payload['party_size']}",
                    f"报价：{operation.quote.currency} "
                    f"{operation.quote.amount_minor / 100:.2f}",
                    f"报价有效期：{operation.quote.valid_until}",
                    f"审批指纹：{operation.approval_sha256}",
                )
            ),
            language=None,
        )
        if operation.status == "pending_approval":
            st.warning("请求已保存，但尚未审批，也未调用任何 provider。")
            if st.button(
                "由独立演示审批人确认精确报价",
                key=f"approve-{operation.operation_id}",
                use_container_width=True,
            ):
                approve_sandbox_booking(_operation_service(), operation)
                st.rerun()
        elif operation.status == "approved":
            st.info("审批已留痕；沙箱 worker 尚未执行。")
            if st.button(
                "运行沙箱 worker",
                key=f"execute-{operation.operation_id}",
                use_container_width=True,
            ):
                executed = execute_next_sandbox_booking(_operation_service())
                if executed is None or executed.operation_id != operation.operation_id:
                    st.warning("worker 本轮没有领取当前操作，请再次检查队列。")
                st.rerun()
        elif operation.status == "uncertain":
            st.warning("provider 调用结果不确定；系统不会自动重试写操作。")
            if st.button(
                "只读核对 provider 状态",
                key=f"reconcile-{operation.operation_id}",
                use_container_width=True,
            ):
                _operation_service().reconcile_uncertain(
                    operation_id=operation.operation_id,
                    tenant_id=operation.tenant_id,
                    actor_id=DEMO_RECONCILER_ID,
                )
                st.rerun()
        elif operation.status == "succeeded":
            st.success("沙箱 provider 已确认；没有真实扣款、商家请求或消息发送。")
            st.code(
                f"receipt_sha256: {operation.receipt_sha256}\n"
                f"provider_operation_id: {operation.provider_operation_id}",
                language=None,
            )
        elif operation.status in {"failed", "denied", "expired"}:
            st.error(
                f"操作已终止：{operation.status} · "
                f"{operation.error_code or operation.denial_reason_code or 'no_provider_write'}"
            )
        else:
            st.info(f"worker 正在处理：{operation.status}")


if __name__ == "__main__":
    main()
