"""L1 anchor cases — 5 case 覆盖 5 强信号，每 commit 跑，目标 30s 内。

每个 case 是一个 dict：
    name:        case 标识
    signal:      S1-S5
    description: 一句话说明
    runner:      callable() -> dict，返回 {pass: bool, observed: any, latency_ms: int}

runner 内部用 mock LLM（BJ_PAL_LLM=mock）保证可重复 + 离线 + 快。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.availability_probe import probe  # noqa: E402
from tools.mock_message import apology_card  # noqa: E402
from tools.prediction_log import (  # noqa: E402
    clear_history,
    record_actual,
    record_prediction,
)
from tools.types import POI  # noqa: E402


def _mock_poi(name: str = "anchor_test", rating: float = 4.3, price: int = 80) -> POI:
    return POI(
        id=f"anchor-{name}",
        name=name,
        category_lv1="餐饮服务",
        category_lv2=None,
        category_lv3=None,
        typecode=None,
        district=None,
        business_area=None,
        address=None,
        longitude=None,
        latitude=None,
        rating=rating,
        avg_price=price,
        open_time=None,
        phone=None,
        photos=[],
    )


# ============================================================
# S3 — 选错容忍度 = 2 次：第 2 次失败必须有 apology
# ============================================================

def check_s3_apology_after_2_fails() -> dict:
    t0 = time.perf_counter()
    poi_name = "anchor_apology_test"
    clear_history(poi_name)
    poi = _mock_poi(poi_name)

    # 第 1 次预测 + 失败回填
    r1 = probe(poi, party_size=2, target_time="14:00", seed=1)
    record_prediction(poi_name, target_time="14:00", predicted_wait_min=r1.wait_min, confidence=r1.confidence)
    record_actual(poi_name, actual_wait_min=45, target_time="14:00")  # 实际等 45min

    # 第 2 次预测：last_prediction_error 应非空
    r2 = probe(poi, party_size=2, target_time="14:30", seed=2)
    has_error_marker = r2.last_prediction_error is not None

    # apology_card 可生成
    card = apology_card(
        contact="@anchor_user",
        poi_name=poi_name,
        last_predicted=f"{r1.wait_min} 分钟",
        actual_observed="45 分钟",
        new_confidence=0.6,
        suggestion="改去同片区第二家",
    )
    has_card = card is not None and len(str(card)) > 0

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    passed = has_error_marker and has_card
    return {
        "pass": passed,
        "observed": {"has_error_marker": has_error_marker, "has_apology_card": has_card},
        "latency_ms": elapsed_ms,
    }


# ============================================================
# S2 — 必须看到吐槽：red_flags 面板可见 + 衰减计算正常
# ============================================================

def check_s2_red_flags_visible() -> dict:
    t0 = time.perf_counter()
    from tools.ugc_signals import extract_red_flags, fetch_aspects, freshness_decay

    # 衰减曲线断言：30 天 food 应 ~0.5
    decay_30 = freshness_decay(30, "food")
    decay_ok = 0.45 <= decay_30 <= 0.55

    # red_flags 应能拉到 ≥ 1 条 negative
    aspects = fetch_aspects(area_anchor="五道营-雍和宫片区")
    flags = extract_red_flags(area_anchor="五道营-雍和宫片区", top_k=1)
    has_flags = len(flags) > 0

    # aspect 都应带 evidence_age_days + dataset_version
    sample_ok = all(
        getattr(a, "evidence_age_days", None) is not None
        and getattr(a, "dataset_version", None) is not None
        for a in aspects[:5]
    ) if aspects else False

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    passed = decay_ok and has_flags and sample_ok
    return {
        "pass": passed,
        "observed": {
            "decay_30d_food": round(decay_30, 3),
            "n_red_flags": len(flags),
            "n_aspects_sampled": min(5, len(aspects)),
            "aspects_have_metadata": sample_ok,
        },
        "latency_ms": elapsed_ms,
    }


# ============================================================
# S5 — 重要场合 = 工具不是代理：触发筛选模式
# ============================================================

def check_s5_screening_mode() -> dict:
    t0 = time.perf_counter()
    from agents.preference_mirror import detect_screening_mode

    # 应触发 screening 的 query
    must_screen = [
        "老婆生日带娃带双方父母 6 人吃饭",
        "朋友结婚纪念日饭",
        "家宴老人首次见",
    ]
    # 不应触发 screening 的 query
    must_not_screen = [
        "周六下午随便吃个饭",
        "两个人下午溜达",
        "3 人喝咖啡",
    ]

    fails = []
    for q in must_screen:
        if not detect_screening_mode(q):
            fails.append(f"漏判: {q}")
    for q in must_not_screen:
        if detect_screening_mode(q):
            fails.append(f"误判: {q}")

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "pass": len(fails) == 0,
        "observed": {"fails": fails, "n_must_screen": len(must_screen), "n_must_not": len(must_not_screen)},
        "latency_ms": elapsed_ms,
    }


# ============================================================
# S4 — 工作日不属于这个 App：场景边界识别
# ============================================================

def check_s4_weekend_focus() -> dict:
    """主台词聚焦周六下午，对工作日 query 应触发澄清而非直接出 plan。

    检查 detect_weekday_context 行为：
    - 工作日 query → should_clarify=True
    - 周末 query → should_clarify=False
    - "周六中午请假"等混合信号 → 周末覆盖工作日，should_clarify=False
    """
    t0 = time.perf_counter()
    try:
        from agents.preference_mirror import detect_weekday_context
    except ImportError:
        return {
            "pass": False,
            "observed": {"error": "detect_weekday_context 未实现"},
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }

    must_clarify = [
        "周一中午有空一起吃个饭吗",
        "工作日下午想溜达",
        "周三下班后聚一下",
    ]
    must_not_clarify = [
        "周六下午带娃出门",
        "周日去逛南锣",
        "周末双休找地方放空",
        "周五下班后周末聚一下",  # 含周末覆盖
    ]

    fails = []
    for q in must_clarify:
        r = detect_weekday_context(q)
        if not r["should_clarify"]:
            fails.append(f"漏判工作日: {q}")
    for q in must_not_clarify:
        r = detect_weekday_context(q)
        if r["should_clarify"]:
            fails.append(f"误判周末: {q} ({r['day_keyword']})")

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "pass": len(fails) == 0,
        "observed": {
            "fails": fails,
            "n_must_clarify": len(must_clarify),
            "n_must_not": len(must_not_clarify),
        },
        "latency_ms": elapsed_ms,
    }


# ============================================================
# S1 — 选错的责任：plan 应有可验证的 trace（D1 集成点）
# ============================================================

def check_s1_responsibility_trace() -> dict:
    """plan_tracer 是否在每步记录 (decision, confidence, fallback)。

    v2.4 新加的能力。如果 plan_tracer 模块不存在 → fail（提示 D1 待补）。
    """
    t0 = time.perf_counter()
    has_tracer = False
    has_record_step_api = False
    try:
        from agents import plan_tracer
        has_tracer = True
        has_record_step_api = hasattr(plan_tracer, "record_step")
    except ImportError:
        pass

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    passed = has_tracer and has_record_step_api
    return {
        "pass": passed,
        "observed": {"has_plan_tracer": has_tracer, "has_record_step_api": has_record_step_api},
        "latency_ms": elapsed_ms,
    }


# ============================================================
# Case registry
# ============================================================

ANCHOR_CASES = [
    {
        "name": "s1_responsibility_trace",
        "signal": "S1",
        "description": "plan_tracer 每步记录 decision/confidence/fallback",
        "runner": check_s1_responsibility_trace,
    },
    {
        "name": "s2_red_flags_visible",
        "signal": "S2",
        "description": "red_flags 面板可见 + 衰减曲线正确",
        "runner": check_s2_red_flags_visible,
    },
    {
        "name": "s3_apology_after_2_fails",
        "signal": "S3",
        "description": "第 2 次失败后 last_prediction_error 非空 + apology_card 可生成",
        "runner": check_s3_apology_after_2_fails,
    },
    {
        "name": "s4_weekend_focus",
        "signal": "S4",
        "description": "工作日 context 识别（baseline 故意 fail，留 todo）",
        "runner": check_s4_weekend_focus,
    },
    {
        "name": "s5_screening_mode",
        "signal": "S5",
        "description": "重要场合关键词触发 screening_mode",
        "runner": check_s5_screening_mode,
    },
]
