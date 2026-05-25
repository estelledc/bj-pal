"""mock 余位 / 排队探针。

W1 D5 交付物。

设计：
- 真实 API 在生产环境对接美团商家 / 哗啦啦 / 客如云 SaaS 排号系统
- Mock 模式：基于 UGC risk_tags + time_bucket 规则触发 wait_min / unavailable
- 关键 demo 素材：trap POI 列表（雍和宫 / 故宫 / 中国科技馆 等）确保
  现场 demo 时一定能触发 reroute

接口：
    probe(poi, party_size, target_time) -> ProbeResult
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .prediction_log import (  # noqa: E402
    degraded_confidence,
    get_last_error,
    record_prediction,
)
from .types import POI  # noqa: E402
from .ugc_signals import fetch_risk_signals  # noqa: E402

ProbeStatus = Literal["ok", "crowd_warn", "unavailable", "closed", "weather_block", "user_dissent"]
RerouteReason = Literal["queue", "weather", "closed", "user_dissent", "merchant_reject", "none"]


@dataclass
class ProbeResult:
    poi_id: Optional[str]
    poi_name: str
    status: ProbeStatus
    wait_min: int = 0
    party_size: int = 1
    target_time: str = ""
    evidence: list[str] = field(default_factory=list)
    risk_tags: list[str] = field(default_factory=list)
    fallback_action: Literal["proceed", "warn", "reroute"] = "proceed"
    reason: RerouteReason = "none"   # v2 改 4：reroute 原因分类
    confidence: float = 0.8          # P0.5：本次预测可信度，被 last_error 拉低
    last_prediction_error: Optional[dict] = None  # P0.5：上次偏差信息，UI 显示标记

    def to_dict(self) -> dict:
        return {
            "poi_id": self.poi_id,
            "poi_name": self.poi_name,
            "status": self.status,
            "wait_min": self.wait_min,
            "party_size": self.party_size,
            "target_time": self.target_time,
            "evidence": list(self.evidence),
            "risk_tags": list(self.risk_tags),
            "fallback_action": self.fallback_action,
            "confidence": self.confidence,
            "last_prediction_error": self.last_prediction_error,
        }


# ============================================================
# Trap POI 配置（demo 关键）
# ============================================================
# 这些 POI 在 demo 时**确定会触发 reroute**，不靠概率。
# 选择标准：UGC 中有高置信度负面 queue/booking_risk 信号，且评委一眼能认。
TRAP_POIS: dict[str, dict] = {
    "雍和宫": {
        "default_wait_min": 85,
        "evidence": [
            "评价：'雍和宫假期人多、入口排队明显、需要提前规划入口和时间' (UGC conf=0.86)",
            "评价：'西门可能不是游客入口或动线较绕，建议确认主入口' (UGC conf=0.78)",
        ],
        "risk_tags": ["holiday_crowd", "entrance_queue", "entrance_confusing"],
    },
    "故宫博物院": {
        "default_wait_min": 120,
        "evidence": [
            "评价：'热门景点人多、预约/安检/入口动线等风险，应提前确认预约与入场' (UGC conf=0.88)",
        ],
        "risk_tags": ["advance_booking_required", "queue", "security_check"],
    },
    "中国科学技术馆": {
        "default_wait_min": 60,
        "evidence": [
            "评价：'热门项目需要排队、最好提前一周预约' (UGC conf=0.86)",
        ],
        "risk_tags": ["advance_booking_needed", "popular_exhibit_queue"],
    },
    "京兆尹(雍和宫店)": {
        "default_wait_min": 45,
        "evidence": [
            "高端餐厅预订紧张，周末晚餐需提前 3 天",
        ],
        "risk_tags": ["high_demand_booking"],
    },
}


# ============================================================
# 主接口
# ============================================================

# v2 改 4：天气模拟（命题"周六下午"硬编码 14:00-15:30 小阵雨）
WEATHER_RAIN_WINDOW = ("14:00", "15:30")  # demo 控制时间窗
WEATHER_OUTDOOR_CATEGORIES = {"风景名胜"}  # 户外类目


def is_weather_blocked(target_time: str, poi: POI) -> Optional[str]:
    """命题硬编码：14:00-15:30 北京小阵雨，户外景点不宜带 5 岁娃。"""
    if not target_time:
        return None
    hh = _hh(target_time)
    rain_start = _hh(WEATHER_RAIN_WINDOW[0])
    rain_end = _hh(WEATHER_RAIN_WINDOW[1])
    if hh is None or rain_start is None or rain_end is None:
        return None
    if rain_start <= hh <= rain_end and poi.category_lv1 in WEATHER_OUTDOOR_CATEGORIES:
        return f"⛅ {WEATHER_RAIN_WINDOW[0]}-{WEATHER_RAIN_WINDOW[1]} 北京小阵雨预警，{poi.name} 是户外景点"
    return None


def _apply_self_evaluation(
    result: ProbeResult,
    base_confidence: float = 0.8,
    record: bool = True,
) -> ProbeResult:
    """P0.5：注入"上次预测偏差"标记 + 降级 confidence + 记录本次预测。

    用法：每个 probe() 出口 return 前包一层。
    """
    last_err = get_last_error(result.poi_name)
    if last_err:
        result.confidence = degraded_confidence(base_confidence, last_err)
        marker = (
            f"⚠ 上次预测偏差 {last_err['error_min']} 分钟"
            f"（说 {last_err['predicted']}min / 实际 {last_err['actual']}min，"
            f"{last_err['days_ago']} 天前），可信度已自动降至 {result.confidence:.1f}"
        )
        # 加到 evidence 顶部（用户最先看到）
        result.evidence = [marker] + list(result.evidence)
        result.last_prediction_error = last_err
    else:
        result.confidence = base_confidence
    if record:
        try:
            record_prediction(
                poi_name=result.poi_name,
                target_time=result.target_time,
                predicted_wait_min=result.wait_min,
                confidence=result.confidence,
            )
        except Exception:
            pass  # log 失败不影响主流程
    return result


def probe(
    poi: POI,
    party_size: int = 3,
    target_time: Optional[str] = None,
    seed: Optional[int] = None,
    enable_weather: bool = True,
    enable_closed: bool = True,
    record_prediction_log: bool = True,
    driving: bool = False,
) -> ProbeResult:
    """检测某 POI 在某时间是否可达 / 排队多久。

    v2 改 4 触发因子（按优先级）：
        1. user_dissent（外部 explicit 调用，不在 probe 内）
        2. weather: 时间窗内户外景点
        3. closed: 5% 概率商家临时停业
        4. queue: trap POI / UGC negative

    P0.5：每次出口前注入 last_prediction_error，并记录本次预测。
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    target_time = target_time or datetime.now().strftime("%H:%M")
    is_weekend_or_holiday = _is_weekend_or_holiday(target_time)
    is_peak_hour = _is_peak_hour(target_time)

    def _finalize(result: ProbeResult) -> ProbeResult:
        return _apply_self_evaluation(result, base_confidence=0.8,
                                      record=record_prediction_log)

    # 0) [10] 古建预约规则——比 weather/trap 都优先（来不及约就是来不及，不用再扯排队）
    from .reservation import check_feasibility
    try:
        from datetime import datetime as _dt
        target_dt = _dt.fromisoformat(target_time) if "T" in target_time else None
    except (ValueError, TypeError):
        target_dt = None
    if target_dt is not None:
        chk = check_feasibility(poi.name, target_dt)
        if chk.requires_reservation and not chk.feasible:
            return _finalize(ProbeResult(
                poi_id=poi.id,
                poi_name=poi.name,
                status="unavailable",
                wait_min=0,
                party_size=party_size,
                target_time=target_time,
                evidence=[
                    chk.reason,
                    f"官方释票：{chk.rule.release_url}" if chk.rule else "",
                ],
                risk_tags=(["reservation_required", "weekly_closed"]
                           if chk.closes_today
                           else ["reservation_required", "reservation_window_closed"]),
                fallback_action=chk.fallback_action,
                reason="closed" if chk.closes_today else "queue",
            ))

    # 1) weather 优先（demo 窗口硬编码触发）
    if enable_weather:
        weather_msg = is_weather_blocked(target_time, poi)
        if weather_msg:
            return _finalize(ProbeResult(
                poi_id=poi.id, poi_name=poi.name,
                status="weather_block", wait_min=0,
                party_size=party_size, target_time=target_time,
                evidence=[weather_msg, "建议改室内活动（博物馆 / 餐厅 / 商场）"],
                risk_tags=["rain", "outdoor"],
                fallback_action="reroute",
                reason="weather",
            ))

    # 0.5) closed 5%（仅餐饮）
    if enable_closed and poi.category_lv1 == "餐饮服务" and rng.random() < 0.05:
        return _finalize(ProbeResult(
            poi_id=poi.id, poi_name=poi.name,
            status="closed", wait_min=0,
            party_size=party_size, target_time=target_time,
            evidence=["商家系统反馈：今日临时停业（设备维护）"],
            risk_tags=["merchant_closed"],
            fallback_action="reroute",
            reason="closed",
        ))

    # 1) Trap POI 命中（hardcoded demo 兜底，最稳）
    if poi.name in TRAP_POIS:
        cfg = TRAP_POIS[poi.name]
        wait = cfg["default_wait_min"]
        if not is_weekend_or_holiday:
            wait = max(15, wait // 2)  # 平日减半
        status: ProbeStatus = "crowd_warn" if wait < 60 else "unavailable"
        action = "reroute" if wait >= 30 else "warn"
        return _finalize(ProbeResult(
            poi_id=poi.id,
            poi_name=poi.name,
            status=status,
            wait_min=wait,
            party_size=party_size,
            target_time=target_time,
            evidence=list(cfg["evidence"]),
            risk_tags=list(cfg["risk_tags"]),
            fallback_action=action,
            reason="queue",
        ))

    # 1.35) [03] 开车模式：停车困难（满 + 等位 ≥ 30min 或无停车场）→ reroute
    if driving and target_dt is not None:
        from .parking import estimate_parking
        est = estimate_parking(poi, target_dt)
        if est.difficulty in ("extreme", "no_parking") and est.wait_min >= 30:
            return _finalize(ProbeResult(
                poi_id=poi.id,
                poi_name=poi.name,
                status="unavailable" if est.difficulty == "no_parking" else "crowd_warn",
                wait_min=min(est.wait_min, 90),
                party_size=party_size,
                target_time=target_time,
                evidence=[est.explanation],
                risk_tags=["no_parking" if est.difficulty == "no_parking" else "parking_extreme"],
                fallback_action="reroute",
                reason="queue",
            ))

    # 1.4) [39] UGC 直方图等位预测——比启发式更准（基于 UGC 真实分钟数）
    from .wait_predictor import predict_wait
    hist_pred = predict_wait(poi.name)
    if hist_pred and hist_pred.confidence >= 0.5 and hist_pred.expected_min >= 30:
        wait = hist_pred.expected_min if is_weekend_or_holiday else max(15, hist_pred.expected_min // 2)
        status_h: ProbeStatus = "crowd_warn" if wait < 60 else "unavailable"
        action_h = "reroute" if wait >= 40 else "warn"
        return _finalize(ProbeResult(
            poi_id=poi.id,
            poi_name=poi.name,
            status=status_h,
            wait_min=wait,
            party_size=party_size,
            target_time=target_time,
            evidence=[
                f"UGC 直方图：{hist_pred.n_samples} 条提及实际等位时长，"
                f"均值 {hist_pred.expected_min}min（p50={hist_pred.p50} / p90={hist_pred.p90}min）",
                f"置信度 {hist_pred.confidence:.1f}（≥ 0.5 才采用此分支）",
            ],
            risk_tags=["ugc_histogram_long_wait"],
            fallback_action=action_h,
            reason="queue",
        ))

    # 1.5) Task 1.3：动态 trap 触发——基于 amap 评分 + UGC negative 信号
    risk_aspects = fetch_risk_signals(poi_name=poi.name)
    trap_score, trap_reasons = compute_dynamic_trap_score(poi, risk_aspects)
    if trap_score >= 0.5:
        wait = int(30 + trap_score * 60)  # 0.5 → 60min, 1.0 → 90min
        if not is_weekend_or_holiday:
            wait = max(15, wait // 2)
        status_d: ProbeStatus = "crowd_warn" if wait < 60 else "unavailable"
        action_d = "reroute" if wait >= 40 else "warn"
        return _finalize(ProbeResult(
            poi_id=poi.id,
            poi_name=poi.name,
            status=status_d,
            wait_min=wait,
            party_size=party_size,
            target_time=target_time,
            evidence=trap_reasons,
            risk_tags=[t for a in risk_aspects for t in a.risk_tags()][:5],
            fallback_action=action_d,
            reason="queue",
        ))

    # 2) UGC negative 信号触发（评分不够"trap"但仍有提示）
    high_neg = [a for a in risk_aspects if a.sentiment == "negative" and a.confidence >= 0.7]
    if high_neg:
        wait = 30 + rng.randint(10, 40)
        if not is_weekend_or_holiday:
            wait //= 2
        return _finalize(ProbeResult(
            poi_id=poi.id,
            poi_name=poi.name,
            status="crowd_warn",
            wait_min=wait,
            party_size=party_size,
            target_time=target_time,
            evidence=[f"UGC[{a.aspect_type}]: {a.evidence_summary[:60]}" for a in high_neg[:2]],
            risk_tags=[t for a in high_neg for t in a.risk_tags()],
            fallback_action="warn" if wait < 40 else "reroute",
            reason="queue",
        ))

    # 3) 默认：基于 rating + 是否餐饮 + 高峰期，给一个温和的 wait
    base_wait = 5
    if poi.category_lv1 == "餐饮服务" and is_peak_hour:
        base_wait += rng.randint(5, 20)
    if poi.rating and poi.rating >= 4.7:
        base_wait += rng.randint(5, 15)  # 高分店通常排队更久
    if is_weekend_or_holiday:
        base_wait += rng.randint(0, 10)

    return _finalize(ProbeResult(
        poi_id=poi.id,
        poi_name=poi.name,
        status="ok",
        wait_min=base_wait,
        party_size=party_size,
        target_time=target_time,
        evidence=[f"无负面 UGC 信号；基线等待 {base_wait}min"],
        risk_tags=[],
        fallback_action="proceed",
        reason="none",
    ))


# ============================================================
# v2 改 4：用户显式 reroute（"换一个"按钮）
# ============================================================

def user_dissent_probe(poi: POI, party_size: int, target_time: str,
                      reason_text: str = "") -> ProbeResult:
    """用户主动点 "换一个"——构造一个 reroute 触发。"""
    text = (reason_text or "不喜欢这个，换一个").strip()
    # 防止反复点"换一个"时 rationale 嵌套"用户反馈：用户反馈：..."
    while text.startswith("用户反馈：") or text.startswith("👤 用户主动反馈："):
        text = text.split("：", 1)[1].strip() if "：" in text else text
    return ProbeResult(
        poi_id=poi.id, poi_name=poi.name,
        status="user_dissent", wait_min=0,
        party_size=party_size, target_time=target_time,
        evidence=[text],
        risk_tags=["user_rejected"],
        fallback_action="reroute",
        reason="user_dissent",
    )


# ============================================================
# helpers
# ============================================================

def _is_weekend_or_holiday(time_str: str) -> bool:
    """命题里小明的场景是周六——demo 默认就 True。

    完整实现在 W2 D2：解析日期 + 节假日 calendar。
    """
    return True


def _is_peak_hour(time_str: str) -> bool:
    """11:30-13:30 / 17:30-20:00 视为餐饮高峰。"""
    hh = _hh(time_str)
    if hh is None:
        return False
    return (11 * 60 + 30) <= hh <= (13 * 60 + 30) or (17 * 60 + 30) <= hh <= (20 * 60)


def _hh(time_str: str) -> Optional[int]:
    """HH:MM 或 ISO → 总分钟。"""
    try:
        if "T" in time_str:
            time_str = time_str.split("T")[1][:5]
        h, m = time_str.split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, IndexError):
        return None


def list_trap_pois() -> list[str]:
    return list(TRAP_POIS.keys())


# ============================================================
# Task 1.3：动态 trap 评分（amap 评分 + UGC 交叉触发）
# ============================================================

def compute_dynamic_trap_score(poi: POI, risk_aspects) -> tuple[float, list[str]]:
    """基于 amap 高评 + UGC 拥堵信号交叉，输出 trap_score [0, 1] + reason 列表。

    评分逻辑：
    - amap rating ≥ 4.7 是必要条件（高分店 + 周末爆棚才是 trap）
    - UGC negative crowd / queue / booking_risk 加分
    - rating 4.5-4.7 段：仅 UGC 强 negative 才能上 0.5
    - 命名含"老字号 / 故宫 / 博物馆"等关键词额外 +0.1（demo 直觉）

    Returns:
        (trap_score, evidence_reasons)
        trap_score ≥ 0.5 即视为 dynamic trap，触发 reroute
    """
    rating = poi.rating or 0.0
    reasons: list[str] = []

    # rating < 4.5 直接返回 0（普通店不算 trap）
    if rating < 4.5:
        return 0.0, []

    score = 0.0

    # 评分维度（最多 0.4）
    if rating >= 4.8:
        score += 0.4
        reasons.append(f"amap 评分 {rating:.1f}，热门程度极高")
    elif rating >= 4.7:
        score += 0.3
        reasons.append(f"amap 评分 {rating:.1f}，热门程度高")
    elif rating >= 4.5:
        score += 0.15

    # UGC negative 维度（最多 0.5）
    queue_neg = [a for a in risk_aspects
                 if a.aspect_type in ("queue", "crowd", "booking_risk")
                 and a.sentiment == "negative" and a.confidence >= 0.6]
    if queue_neg:
        score += min(0.5, 0.2 * len(queue_neg))
        # 取最高 confidence 那条做 evidence
        top = max(queue_neg, key=lambda a: a.confidence)
        reasons.append(
            f"UGC[{top.aspect_type}/conf={top.confidence:.2f}]: {top.evidence_summary[:70]}"
        )

    # 命名启发式（最多 0.15）
    name_keywords = ("老字号", "故宫", "博物馆", "天坛", "颐和园", "雍和宫",
                     "全聚德", "便宜坊", "海底捞", "胡大", "稻香村")
    if any(kw in (poi.name or "") for kw in name_keywords):
        score += 0.15
        reasons.append(f"POI 名含'{[kw for kw in name_keywords if kw in poi.name][0]}'，普遍排队风险高")

    # 高峰时段加分
    # （省略，由调用方的 is_peak_hour 处理 base_wait）

    score = min(1.0, score)
    return round(score, 3), reasons
