"""可解释的步骤证据支持度。

这里故意不把 ToT utility、LLM 自评或规则分数冒充为“成功概率”。
`evidence_support_v1` 只回答：当前步骤被多少可检查证据支撑。
只有积累真实 plan_outcome 后，才能用 ECE 检查或校准它与成功率的关系。
"""

from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from data_profile import DataProfile, load_data_profile

from .types import Plan, Step


EVIDENCE_SUPPORT_SOURCE = "evidence_support_v1"
CONTROL_STEP_SOURCE = "deterministic_control_v1"
CONFIDENCE_SEMANTICS = (
    "evidence support score; not a calibrated probability; "
    "calibration requires paired real outcomes"
)


@dataclass(frozen=True)
class EvidenceSnapshot:
    """一批 plan 所需的最小数据证据，避免每一步重复查库。"""

    rating_by_poi_id: Mapping[str, float]
    ugc_by_poi_name: Mapping[str, tuple[int, float]]


EvidenceLookup = Callable[[Plan], EvidenceSnapshot]


def load_evidence_snapshot(plan: Plan) -> EvidenceSnapshot:
    """从本地 SQLite 批量读取评分与 UGC 厚度；缺库时安全降级为空证据。"""
    poi_ids = sorted({step.poi_id for step in plan.steps if step.poi_id})
    poi_names = sorted({step.poi_name for step in plan.steps if step.poi_name})
    ratings: dict[str, float] = {}
    ugc: dict[str, tuple[int, float]] = {}

    try:
        from loader import get_conn

        with closing(get_conn()) as conn:
            if poi_ids:
                marks = ",".join("?" for _ in poi_ids)
                rows = conn.execute(
                    f"SELECT id, rating FROM pois WHERE id IN ({marks})",
                    poi_ids,
                ).fetchall()
                ratings = {
                    str(row["id"]): float(row["rating"] or 0.0)
                    for row in rows
                }
            if poi_names:
                marks = ",".join("?" for _ in poi_names)
                rows = conn.execute(
                    "SELECT poi_name, COUNT(*) AS n, AVG(confidence) AS avg_conf "
                    f"FROM ugc_aspects WHERE poi_name IN ({marks}) GROUP BY poi_name",
                    poi_names,
                ).fetchall()
                ugc = {
                    str(row["poi_name"]): (
                        int(row["n"] or 0),
                        float(row["avg_conf"] or 0.0),
                    )
                    for row in rows
                }
    except (FileNotFoundError, OSError, sqlite3.Error):
        # 支持在数据库尚未 bootstrap 的纯 schema / unit-test 环境中运行。
        pass

    return EvidenceSnapshot(rating_by_poi_id=ratings, ugc_by_poi_name=ugc)


def estimate_plan_confidence(
    plan: Plan,
    *,
    profile: Optional[DataProfile] = None,
    evidence_lookup: Optional[EvidenceLookup] = None,
) -> Plan:
    """原地补齐每个 Step 的支持度、来源和可解释因子，并返回 plan。"""
    active_profile = profile or load_data_profile()
    snapshot = (evidence_lookup or load_evidence_snapshot)(plan)
    for step in plan.steps:
        value, source, factors = estimate_step_confidence(
            step,
            snapshot=snapshot,
            profile=active_profile,
        )
        step.confidence = value
        step.confidence_source = source
        step.confidence_factors = factors
    return plan


def estimate_step_confidence(
    step: Step,
    *,
    snapshot: EvidenceSnapshot,
    profile: DataProfile,
) -> tuple[float, str, dict]:
    """计算单步支持度；返回值不宣称是成功概率。"""
    if step.kind == "depart":
        factors = {
            "base": 0.95,
            "control_step": True,
            "data_profile": profile.name,
            "data_classification": profile.classification,
            "semantics": CONFIDENCE_SEMANTICS,
        }
        return 0.95, CONTROL_STEP_SOURCE, factors

    base = 0.35
    rating = snapshot.rating_by_poi_id.get(step.poi_id or "", 0.0)
    poi_grounding = 0.12 if step.poi_id and step.poi_id in snapshot.rating_by_poi_id else 0.0
    rating_support = min(max(rating, 0.0), 5.0) / 5.0 * 0.12

    ugc_count, ugc_avg_conf = snapshot.ugc_by_poi_name.get(step.poi_name, (0, 0.0))
    ugc_depth = min(math.log1p(ugc_count) / math.log1p(20), 1.0) if ugc_count else 0.0
    ugc_support = 0.12 * ugc_depth * min(max(ugc_avg_conf, 0.0), 1.0)

    has_route = bool(
        step.travel_time_min > 0
        or step.travel_distance_m > 0
        or step.travel_options
    )
    route_support = 0.08 if has_route else 0.0
    rationale_len = len("".join((step.rationale or "").split()))
    rationale_support = min(rationale_len / 80.0, 1.0) * 0.08

    profile_support = {
        "real": 0.10,
        "verified": 0.10,
        "mixed": 0.05,
    }.get(profile.classification, 0.0)
    risk_penalty = min(len(step.risk_tags or []) * 0.05, 0.15)
    reroute_penalty = 0.08 if step.is_rerouted else 0.0
    booking_support = 0.15 if step.booking else 0.0

    raw = (
        base
        + poi_grounding
        + rating_support
        + ugc_support
        + route_support
        + rationale_support
        + profile_support
        + booking_support
        - risk_penalty
        - reroute_penalty
    )
    cap = 0.79 if profile.contains_synthetic_data else 0.92
    value = round(max(0.0, min(cap, raw)), 3)
    factors = {
        "base": base,
        "poi_grounding": round(poi_grounding, 3),
        "rating": round(rating, 3),
        "rating_support": round(rating_support, 3),
        "ugc_count": ugc_count,
        "ugc_avg_confidence": round(ugc_avg_conf, 3),
        "ugc_support": round(ugc_support, 3),
        "route_support": round(route_support, 3),
        "rationale_support": round(rationale_support, 3),
        "profile_support": round(profile_support, 3),
        "booking_support": round(booking_support, 3),
        "risk_penalty": round(risk_penalty, 3),
        "reroute_penalty": round(reroute_penalty, 3),
        "cap": cap,
        "data_profile": profile.name,
        "data_classification": profile.classification,
        "semantics": CONFIDENCE_SEMANTICS,
    }
    return value, EVIDENCE_SUPPORT_SOURCE, factors
