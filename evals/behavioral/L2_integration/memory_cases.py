"""L2 user_memory 5 case — 跨 session 偏好沉淀 / 注入 / 衰减。"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.planner import plan  # noqa: E402
from agents.user_memory import (  # noqa: E402
    forget,
    forget_all,
    get_preferences,
    infer_from_user_input,
    merge_into_prompt,
    record_preference,
)


def _new_uid() -> str:
    return f"l2-mem-{uuid.uuid4().hex[:8]}"


def _e2e_cross_session_runner():
    def _r() -> dict:
        t0 = time.perf_counter()
        uid = _new_uid()
        try:
            # 第 1 次 plan：含偏好关键词
            plan(user_input="带 5 岁娃出去玩，老婆减脂不吃辣，找咖啡店",
                 persona="family", user_id=uid)
            prefs1 = get_preferences(uid)
            keys1 = [p.mem_key for p in prefs1]

            # 第 2 次 plan：query 不再提偏好
            plan(user_input="这周末再去玩", persona="family", user_id=uid)
            prefs2 = get_preferences(uid)
            keys2 = [p.mem_key for p in prefs2]

            ok = (
                any("light_diet" in k for k in keys1)
                and any("with_child" in k for k in keys1)
                and any("light_diet" in k for k in keys2)   # 跨 session 仍存在
                and any("with_child" in k for k in keys2)
                and len(prefs2) >= len(prefs1)              # 不丢
            )
            elapsed = int((time.perf_counter() - t0) * 1000)
            return {
                "pass": ok,
                "observed": {
                    "round1_keys": keys1[:6],
                    "round2_keys": keys2[:6],
                    "round1_count": len(prefs1),
                    "round2_count": len(prefs2),
                },
                "latency_ms": elapsed,
            }
        finally:
            forget_all(uid)
    return _r


def _negation_disambiguation_runner():
    def _r() -> dict:
        t0 = time.perf_counter()
        uid = _new_uid()
        try:
            infer_from_user_input(uid, "我老婆不吃辣，避开海鲜")
            prefs = get_preferences(uid)
            spicy = [p for p in prefs if p.mem_key == "taste:spicy"]
            elapsed = int((time.perf_counter() - t0) * 1000)
            ok = len(spicy) == 0 or spicy[0].kind == "dislike"
            return {
                "pass": ok,
                "observed": {
                    "spicy_kind": spicy[0].kind if spicy else "(none)",
                    "all_keys": [p.mem_key for p in prefs],
                },
                "latency_ms": elapsed,
            }
        finally:
            forget_all(uid)
    return _r


def _forget_resurrect_runner():
    def _r() -> dict:
        t0 = time.perf_counter()
        uid = _new_uid()
        try:
            record_preference(uid, "taste:coffee", True)
            forget(uid, "taste:coffee")
            after_forget = get_preferences(uid)
            record_preference(uid, "taste:coffee", True)
            after_resurrect = get_preferences(uid)
            elapsed = int((time.perf_counter() - t0) * 1000)
            ok = len(after_forget) == 0 and len(after_resurrect) == 1
            return {
                "pass": ok,
                "observed": {"after_forget": len(after_forget),
                             "after_resurrect": len(after_resurrect)},
                "latency_ms": elapsed,
            }
        finally:
            forget_all(uid)
    return _r


def _merge_threshold_runner():
    def _r() -> dict:
        t0 = time.perf_counter()
        uid = _new_uid()
        try:
            record_preference(uid, "taste:fruit", True, confidence=0.30)
            record_preference(uid, "taste:coffee", True, confidence=0.85)
            merged = merge_into_prompt("4 人下午", uid, confidence_threshold=0.5)
            elapsed = int((time.perf_counter() - t0) * 1000)
            ok = "coffee" in merged and "fruit" not in merged
            return {
                "pass": ok,
                "observed": {"merged_preview": merged[-200:]},
                "latency_ms": elapsed,
            }
        finally:
            forget_all(uid)
    return _r


def _user_isolation_runner():
    """不同 user_id 的偏好互不干扰。"""
    def _r() -> dict:
        t0 = time.perf_counter()
        uid_a = _new_uid()
        uid_b = _new_uid()
        try:
            record_preference(uid_a, "diet:light_diet", True)
            record_preference(uid_b, "taste:meat", True)
            a = [p.mem_key for p in get_preferences(uid_a)]
            b = [p.mem_key for p in get_preferences(uid_b)]
            elapsed = int((time.perf_counter() - t0) * 1000)
            ok = (
                "diet:light_diet" in a and "taste:meat" not in a
                and "taste:meat" in b and "diet:light_diet" not in b
            )
            return {
                "pass": ok,
                "observed": {"user_a_keys": a, "user_b_keys": b},
                "latency_ms": elapsed,
            }
        finally:
            forget_all(uid_a)
            forget_all(uid_b)
    return _r


CASES = [
    {
        "name": "mem1_e2e_cross_session",
        "capability": "user_memory",
        "description": "跨 session：第 1 次提偏好 → 第 2 次不重复 → 仍生效",
        "runner": _e2e_cross_session_runner(),
    },
    {
        "name": "mem2_negation_to_dislike",
        "capability": "user_memory",
        "description": "「不吃辣」抽到 dislike，不是 preference",
        "runner": _negation_disambiguation_runner(),
    },
    {
        "name": "mem3_forget_resurrect",
        "capability": "user_memory",
        "description": "forget 后再 record 自动复活 forgotten 条目",
        "runner": _forget_resurrect_runner(),
    },
    {
        "name": "mem4_low_conf_filtered",
        "capability": "user_memory",
        "description": "merge_into_prompt 过滤 confidence < threshold 的偏好",
        "runner": _merge_threshold_runner(),
    },
    {
        "name": "mem5_user_isolation",
        "capability": "user_memory",
        "description": "不同 user_id 偏好互不干扰",
        "runner": _user_isolation_runner(),
    },
]
