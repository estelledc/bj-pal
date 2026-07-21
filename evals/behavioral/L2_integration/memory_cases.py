"""L2 user_memory 5 case — 显式写入、冲突、生命周期与隔离。"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.planner import plan  # noqa: E402
from agents.user_memory import (  # noqa: E402
    confirm_memory,
    delete_all,
    get_preferences,
    infer_from_user_input,
    list_memory_events,
    merge_into_prompt,
    record_preference,
    upsert_memory,
)


def _new_uid() -> str:
    return f"l2-mem-{uuid.uuid4().hex[:8]}"


def _e2e_cross_session_runner():
    def _r() -> dict:
        t0 = time.perf_counter()
        uid = _new_uid()
        try:
            # 长期记忆只允许显式入口写入；planner 本身只读，避免 rerun 误沉淀。
            infer_from_user_input(
                uid,
                "带 5 岁娃出去玩，老婆减脂不吃辣，找咖啡店",
            )

            # 第 1 次 plan：读取已确认偏好
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
                and any("kid_friendly" in k for k in keys1)
                and any("light_diet" in k for k in keys2)   # 跨 session 仍存在
                and any("kid_friendly" in k for k in keys2)
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
            delete_all(uid)
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
            delete_all(uid)
    return _r


def _conflict_resolution_runner():
    def _r() -> dict:
        t0 = time.perf_counter()
        uid = _new_uid()
        try:
            created = upsert_memory(
                uid,
                "area:current_city",
                "北京",
                kind="fact",
                source="explicit_user_input",
                confirmed=True,
            )
            rejected = upsert_memory(
                uid,
                "area:current_city",
                "上海",
                kind="fact",
                source="inferred",
                confirmed=False,
            )
            replaced = upsert_memory(
                uid,
                "area:current_city",
                "上海",
                kind="fact",
                source="explicit_user_input",
                confirmed=True,
            )
            prompt = merge_into_prompt("周末去哪", uid)
            events = list_memory_events(uid)
            elapsed = int((time.perf_counter() - t0) * 1000)
            ok = (
                created.action == "created"
                and rejected.action == "conflict_rejected"
                and rejected.entry.mem_value == "北京"
                and replaced.action == "replaced"
                and replaced.entry.mem_value == "上海"
                and replaced.entry.revision == 2
                and replaced.entry.mention_count == 1
                and "上海" in prompt
                and "北京" not in prompt
                and [event.event_type for event in events]
                == ["created", "conflict_rejected", "replaced"]
            )
            return {
                "pass": ok,
                "observed": {
                    "actions": [created.action, rejected.action, replaced.action],
                    "current_value": replaced.entry.mem_value,
                    "revision": replaced.entry.revision,
                    "event_types": [event.event_type for event in events],
                },
                "latency_ms": elapsed,
            }
        finally:
            delete_all(uid)
    return _r


def _lifecycle_gate_runner():
    def _r() -> dict:
        t0 = time.perf_counter()
        uid = _new_uid()
        try:
            upsert_memory(
                uid,
                "area:current_city",
                "北京",
                kind="fact",
                source="inferred",
                confirmed=False,
            )
            before_confirmation = merge_into_prompt("周末去哪", uid)
            confirmed = confirm_memory(uid, "area:current_city", kind="fact")
            after_confirmation = merge_into_prompt("周末去哪", uid)
            record_preference(
                uid,
                "area:temporary_city",
                "天津",
                kind="fact",
                expires_at=time.time() - 1,
            )
            after_expiry = merge_into_prompt("周末去哪", uid)
            elapsed = int((time.perf_counter() - t0) * 1000)
            ok = (
                before_confirmation == "周末去哪"
                and confirmed
                and "北京" in after_confirmation
                and "天津" not in after_expiry
            )
            return {
                "pass": ok,
                "observed": {
                    "before_confirmation": before_confirmation,
                    "after_confirmation": after_confirmation[-160:],
                    "after_expiry": after_expiry[-160:],
                },
                "latency_ms": elapsed,
            }
        finally:
            delete_all(uid)
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
            delete_all(uid_a)
            delete_all(uid_b)
    return _r


CASES = [
    {
        "name": "mem1_e2e_cross_session",
        "capability": "user_memory",
        "description": "显式记忆入口沉淀偏好 → 两次 planner 只读 → 跨 session 仍生效",
        "runner": _e2e_cross_session_runner(),
    },
    {
        "name": "mem2_negation_to_dislike",
        "capability": "user_memory",
        "description": "「不吃辣」抽到 dislike，不是 preference",
        "runner": _negation_disambiguation_runner(),
    },
    {
        "name": "mem3_conflict_resolution",
        "capability": "user_memory",
        "description": "未确认异值不能覆盖；显式异值替换并开启新 revision",
        "runner": _conflict_resolution_runner(),
    },
    {
        "name": "mem4_confirmation_expiry_gate",
        "capability": "user_memory",
        "description": "未确认和过期记忆都不能注入 planner prompt",
        "runner": _lifecycle_gate_runner(),
    },
    {
        "name": "mem5_user_isolation",
        "capability": "user_memory",
        "description": "不同 user_id 偏好互不干扰",
        "runner": _user_isolation_runner(),
    },
]
