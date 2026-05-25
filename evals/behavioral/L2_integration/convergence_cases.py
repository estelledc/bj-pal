"""L2 group_convergence 5 case — 不同群体规模 / 模式下的收敛行为。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.group_convergence import run_convergence_loop  # noqa: E402
from agents.group_dynamics import (  # noqa: E402
    classify_member,
    measure_convergence,
    profile_group,
)
from agents.planner import plan  # noqa: E402
from agents.types import UserPreferences  # noqa: E402
from tools.mock_message import (  # noqa: E402
    DEMO_FRIEND_GROUP,
    ContactResponse,
    GroupMember,
)


def _convergence_target_runner(query: str, max_rounds: int = 4,
                                seed: int = 42, target_rounds: int = 2):
    """跑 e2e convergence loop，验收 ≤ target_rounds 收敛。"""
    def _r() -> dict:
        t0 = time.perf_counter()
        prefs = UserPreferences(persona="friends", raw_input=query)
        p = plan(user_input=query, persona="friends", prefs=prefs)
        report = run_convergence_loop(p, DEMO_FRIEND_GROUP, prefs=prefs,
                                       max_rounds=max_rounds, rng_seed=seed)
        elapsed = int((time.perf_counter() - t0) * 1000)
        ok = report.converged and report.rounds_used <= target_rounds
        return {
            "pass": ok,
            "observed": {
                "converged": report.converged,
                "rounds_used": report.rounds_used,
                "target": target_rounds,
                "reason": report.reason,
            },
            "latency_ms": elapsed,
        }
    return _r


def _classify_pattern_runner(history: list, expect_pattern: str,
                              first_responder_name: str | None = None):
    def _r() -> dict:
        t0 = time.perf_counter()
        member = GroupMember(name="@测试", avatar_emoji="👤")
        is_first = (first_responder_name == "@测试")
        p = classify_member(member, history, broadcast_seq_index_first=is_first)
        elapsed = int((time.perf_counter() - t0) * 1000)
        return {
            "pass": p.pattern == expect_pattern,
            "observed": {"got_pattern": p.pattern, "expected": expect_pattern,
                         "weight": p.weight},
            "latency_ms": elapsed,
        }
    return _r


CASES = [
    {
        "name": "cv1_friends_group_2round",
        "capability": "group_convergence",
        "description": "标准 4 人 friends 群应 ≤ 2 轮收敛",
        "runner": _convergence_target_runner(
            "4 人雍和宫片区下午溜达吃饭",
            max_rounds=3, seed=42, target_rounds=2,
        ),
    },
    {
        "name": "cv2_alt_seed_convergence",
        "capability": "group_convergence",
        "description": "不同 seed 仍 ≤ 3 轮收敛（鲁棒性）",
        "runner": _convergence_target_runner(
            "4 人朋友周六下午吃饭",
            max_rounds=4, seed=99, target_rounds=3,
        ),
    },
    {
        "name": "cv3_classify_vetoer",
        "capability": "group_convergence",
        "description": "rejected ≥ 2 → vetoer (weight 0.5)",
        "runner": _classify_pattern_runner(
            [
                ContactResponse(contact="@测试", avatar="👤", status="rejected",
                                rejection_reason="spicy"),
                ContactResponse(contact="@测试", avatar="👤", status="rejected",
                                rejection_reason="loud"),
            ],
            expect_pattern="vetoer",
        ),
    },
    {
        "name": "cv4_classify_silent",
        "capability": "group_convergence",
        "description": "no_reply ≥ 2 → silent (weight 0.7)",
        "runner": _classify_pattern_runner(
            [
                ContactResponse(contact="@测试", avatar="👤", status="no_reply"),
                ContactResponse(contact="@测试", avatar="👤", status="no_reply"),
            ],
            expect_pattern="silent",
        ),
    },
    {
        "name": "cv5_classify_implicit_leader",
        "capability": "group_convergence",
        "description": "首响应 + leader phrase + confirmed → implicit_leader (1.5x)",
        "runner": _classify_pattern_runner(
            [
                ContactResponse(contact="@测试", avatar="👤", status="confirmed",
                                reply_text="听我的就这家！", reply_at_ms=100),
            ],
            expect_pattern="implicit_leader",
            first_responder_name="@测试",
        ),
    },
]
