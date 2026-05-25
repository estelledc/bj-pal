"""L3 信号检查器：5 强信号自动验证。

每个 check 函数签名：
    check_sX(case: L3Case) -> dict {pass: bool, observed: ..., latency_ms: int}

不实际跑 plan 多次（成本太高），而是基于已有能力做静态/动态判定：
- S1 plan_tracer 覆盖 + fallback_action 存在
- S2 候选池有负面 aspect 可拉
- S3 多次失败后 apology 链路通
- S4 detect_weekday_context 应触发澄清
- S5 detect_screening_mode 应触发筛选
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.plan_tracer import coverage_rate, iter_steps  # noqa: E402
from agents.planner import plan as make_plan  # noqa: E402
from agents.preference_mirror import (  # noqa: E402
    detect_screening_mode,
    detect_weekday_context,
)
from agents.types import UserPreferences  # noqa: E402
from tools.availability_probe import probe  # noqa: E402
from tools.mock_message import apology_card  # noqa: E402
from tools.prediction_log import (  # noqa: E402
    clear_history,
    record_actual,
    record_prediction,
)
from tools.types import POI  # noqa: E402
from tools.ugc_signals import extract_red_flags, fetch_aspects  # noqa: E402

from .fixtures import L3Case  # noqa: E402


# ============================================================
# Plan 缓存（同 query 不重复 plan，节省 mock 时间）
# ============================================================

_PLAN_CACHE: dict[str, object] = {}


def _cached_plan(case: L3Case):
    key = f"{case.persona}::{case.query}"
    if key in _PLAN_CACHE:
        return _PLAN_CACHE[key]
    prefs = UserPreferences(
        persona=case.persona,
        raw_input=case.query,
        target_start="14:00",
        duration_hours=4.0,
    )
    p = make_plan(user_input=case.query, persona=case.persona, prefs=prefs)
    _PLAN_CACHE[key] = p
    return p


# ============================================================
# S1 责任承担：plan_tracer 覆盖 + fallback_action 存在
# ============================================================

def check_s1(case: L3Case) -> dict:
    t0 = time.perf_counter()
    p = _cached_plan(case)
    cov = coverage_rate(p.plan_id, expected_steps=len(p.steps))
    traces = iter_steps(p.plan_id)
    has_fallback = any(t.fallback_action for t in traces)
    elapsed = int((time.perf_counter() - t0) * 1000)
    return {
        "pass": cov == 1.0 and has_fallback,
        "observed": {"coverage": cov, "n_traces": len(traces),
                     "has_fallback": has_fallback,
                     "plan_id": p.plan_id},
        "latency_ms": elapsed,
    }


# ============================================================
# S2 看到吐槽：plan area_anchor 内能拉到 ≥ 1 条 negative red_flag
# ============================================================

def check_s2(case: L3Case) -> dict:
    t0 = time.perf_counter()
    p = _cached_plan(case)
    flags = extract_red_flags(area_anchor=p.area_anchor, top_k=3)
    aspects = fetch_aspects(area_anchor=p.area_anchor)
    has_metadata = bool(aspects) and aspects[0].evidence_age_days is not None
    elapsed = int((time.perf_counter() - t0) * 1000)
    return {
        "pass": len(flags) >= 1 and has_metadata,
        "observed": {"area_anchor": p.area_anchor,
                     "n_red_flags": len(flags),
                     "n_aspects": len(aspects),
                     "metadata_complete": has_metadata},
        "latency_ms": elapsed,
    }


# ============================================================
# S3 选错容忍：第 2 次失败后 apology + last_prediction_error
# ============================================================

def check_s3(case: L3Case) -> dict:
    t0 = time.perf_counter()
    poi_name = f"l3_test_{case.case_id[:16]}"
    clear_history(poi_name)
    poi = POI(
        id=f"l3-{case.case_id[:8]}", name=poi_name,
        category_lv1="餐饮服务", category_lv2=None, category_lv3=None,
        typecode=None, district=None, business_area=None, address=None,
        longitude=None, latitude=None, rating=4.3, avg_price=80,
        open_time=None, phone=None, photos=[],
    )
    r1 = probe(poi, party_size=2, target_time="14:00", seed=1)
    record_prediction(poi_name, target_time="14:00",
                      predicted_wait_min=r1.wait_min, confidence=r1.confidence)
    record_actual(poi_name, actual_wait_min=45, target_time="14:00")
    r2 = probe(poi, party_size=2, target_time="14:30", seed=2)
    has_error_marker = r2.last_prediction_error is not None
    card = apology_card(
        contact="@l3_user", poi_name=poi_name,
        last_predicted=f"{r1.wait_min}分钟", actual_observed="45分钟",
        new_confidence=0.6, suggestion="改去同片区第二家",
    )
    has_card = card is not None and len(str(card)) > 0
    elapsed = int((time.perf_counter() - t0) * 1000)
    return {
        "pass": has_error_marker and has_card,
        "observed": {"has_error_marker": has_error_marker,
                     "has_apology_card": has_card},
        "latency_ms": elapsed,
    }


# ============================================================
# S4 工作日不属于：weekday query 应触发 should_clarify
# ============================================================

def check_s4(case: L3Case) -> dict:
    t0 = time.perf_counter()
    r = detect_weekday_context(case.query)
    elapsed = int((time.perf_counter() - t0) * 1000)
    # 仅在工作日场景判定 — case.expected_signals 含 S4 时必须命中
    return {
        "pass": r["should_clarify"],
        "observed": r,
        "latency_ms": elapsed,
    }


# ============================================================
# S5 重要场合：detect_screening_mode 应触发
# ============================================================

def check_s5(case: L3Case) -> dict:
    t0 = time.perf_counter()
    triggered = detect_screening_mode(case.query)
    elapsed = int((time.perf_counter() - t0) * 1000)
    return {
        "pass": triggered,
        "observed": {"triggered": triggered, "query": case.query},
        "latency_ms": elapsed,
    }


# ============================================================
# 总入口
# ============================================================

SIGNAL_CHECKERS = {
    "S1": check_s1,
    "S2": check_s2,
    "S3": check_s3,
    "S4": check_s4,
    "S5": check_s5,
}


def check_all_signals(case: L3Case) -> dict[str, dict]:
    """对单 case 跑所有期望信号的检查。"""
    out: dict[str, dict] = {}
    for sig in case.expected_signals:
        checker = SIGNAL_CHECKERS.get(sig)
        if checker is None:
            out[sig] = {"pass": False, "observed": {"error": f"unknown signal {sig}"},
                        "latency_ms": 0}
            continue
        try:
            out[sig] = checker(case)
        except Exception as exc:
            out[sig] = {
                "pass": False,
                "observed": {"error": f"{type(exc).__name__}: {exc}"},
                "latency_ms": 0,
            }
    return out


def reset_plan_cache():
    _PLAN_CACHE.clear()
