"""D5 群偏好收敛器 — 从 broadcast 历史检测成员行为模式。

参考：docs/V2.4_ITERATION_PLAN.md Round 5

检测三类模式（基于一系列 ContactResponse）：

| 模式 | 触发信号 | weight 影响 |
|---|---|---|
| vetoer (反复横跳) | rejected ≥ 2 OR rejected ↔ waiting 频繁切换 | 0.5（降权，避免无限 reroute） |
| silent (沉默)  | no_reply ≥ 2 OR 单 broadcast 5min+ 无响应 | 0.7（降权但不完全忽略） |
| implicit_leader (隐性领导) | 第 1 句 confirmed 且关键短语 | 1.5（升权） |
| normal | 其他 | 1.0 |

输出 MemberProfile 注入 group_harmony.group_rank 的 weighted 模式。

非 LLM 路径：纯规则 + 关键词，保证可重复 + 离线可跑。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mock_message import ContactResponse, GroupMember  # noqa: E402

MemberPattern = Literal["normal", "vetoer", "silent", "implicit_leader"]


# ============================================================
# 关键词与阈值
# ============================================================

LEADER_PHRASES = [
    "我觉得", "听我的", "就这家", "就去", "我决定", "我定", "定了",
    "别犹豫", "我说", "我提议", "必须",
]

VETOER_REJECTION_THRESHOLD = 2
SILENT_NO_REPLY_THRESHOLD = 2
SILENT_TIMEOUT_MS = 5 * 60 * 1000  # 5min

WEIGHT_BY_PATTERN: dict[MemberPattern, float] = {
    "normal": 1.0,
    "vetoer": 0.5,
    "silent": 0.7,
    "implicit_leader": 1.5,
}


# ============================================================
# 数据结构
# ============================================================

@dataclass
class MemberProfile:
    name: str
    pattern: MemberPattern
    weight: float
    evidence: dict   # 触发该 pattern 的具体信号

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "pattern": self.pattern,
            "weight": self.weight,
            "evidence": self.evidence,
        }


# ============================================================
# 单次 broadcast 内单成员的所有响应
# ============================================================

def _flip_count(history: list[ContactResponse]) -> int:
    """rejected ↔ confirmed/waiting 切换次数。"""
    if len(history) < 2:
        return 0
    flips = 0
    last_class = _status_class(history[0].status)
    for r in history[1:]:
        cur = _status_class(r.status)
        if cur != last_class and last_class != "ignore":
            flips += 1
        last_class = cur
    return flips


def _status_class(status: str) -> str:
    if status == "rejected":
        return "negative"
    if status in ("confirmed", "waiting"):
        return "positive"
    return "ignore"


def _has_leader_phrase(reply_text: str) -> bool:
    if not reply_text:
        return False
    for phrase in LEADER_PHRASES:
        if phrase in reply_text:
            return True
    return False


# ============================================================
# 主接口：classify_member
# ============================================================

def classify_member(
    member: GroupMember,
    history: list[ContactResponse],
    *,
    broadcast_seq_index_first: bool = False,
) -> MemberProfile:
    """根据某成员在过去 broadcast 中的 ContactResponse 序列分类。

    Args:
        member: 成员
        history: 该成员的所有响应（按时间升序）
        broadcast_seq_index_first: 该成员是否每次都最早响应（leader 信号之一）

    Returns:
        MemberProfile
    """
    if not history:
        return MemberProfile(
            name=member.name,
            pattern="silent",
            weight=WEIGHT_BY_PATTERN["silent"],
            evidence={"reason": "无任何响应"},
        )

    n_rejected = sum(1 for r in history if r.status == "rejected")
    n_no_reply = sum(1 for r in history if r.status == "no_reply")
    flips = _flip_count(history)

    first_confirmed = history[0].status == "confirmed"
    first_has_phrase = _has_leader_phrase(history[0].reply_text)

    # 优先级：implicit_leader > vetoer > silent > normal
    if first_confirmed and first_has_phrase and broadcast_seq_index_first:
        return MemberProfile(
            name=member.name,
            pattern="implicit_leader",
            weight=WEIGHT_BY_PATTERN["implicit_leader"],
            evidence={
                "first_confirmed": True,
                "leader_phrase_in_first_reply": True,
                "first_to_respond": True,
                "phrase_sample": history[0].reply_text[:30],
            },
        )

    if n_rejected >= VETOER_REJECTION_THRESHOLD or flips >= 2:
        return MemberProfile(
            name=member.name,
            pattern="vetoer",
            weight=WEIGHT_BY_PATTERN["vetoer"],
            evidence={
                "n_rejected": n_rejected,
                "flips": flips,
                "rejection_reasons": [r.rejection_reason for r in history if r.status == "rejected"][:3],
            },
        )

    if n_no_reply >= SILENT_NO_REPLY_THRESHOLD:
        return MemberProfile(
            name=member.name,
            pattern="silent",
            weight=WEIGHT_BY_PATTERN["silent"],
            evidence={"n_no_reply": n_no_reply, "n_total": len(history)},
        )

    return MemberProfile(
        name=member.name,
        pattern="normal",
        weight=WEIGHT_BY_PATTERN["normal"],
        evidence={"n_responses": len(history), "n_rejected": n_rejected},
    )


# ============================================================
# 多成员批量
# ============================================================

def profile_group(
    members: list[GroupMember],
    history_by_member: dict[str, list[ContactResponse]],
    first_responder: str | None = None,
) -> dict[str, MemberProfile]:
    """批量分类。

    Args:
        members: 全部成员
        history_by_member: {member.name: [ContactResponse, ...]}
        first_responder: 在最近一次 broadcast 中第一个响应的成员 name（leader 信号）

    Returns:
        {name: MemberProfile}
    """
    out: dict[str, MemberProfile] = {}
    for m in members:
        history = history_by_member.get(m.name, [])
        is_first = (m.name == first_responder)
        out[m.name] = classify_member(m, history, broadcast_seq_index_first=is_first)
    return out


# ============================================================
# 收敛轮次度量（v2.4 D5 度量目标 ≤ 2 轮）
# ============================================================

@dataclass
class ConvergenceResult:
    converged: bool
    rounds: int
    reason: str   # "consensus" / "max_rounds" / "veto_loop"


def measure_convergence(
    broadcast_rounds: list[dict],
    max_rounds: int = 4,
) -> ConvergenceResult:
    """从 broadcast 多轮序列度量收敛轮次。

    Args:
        broadcast_rounds: 每轮一个 dict，含 "n_confirmed", "n_rejected", "n_no_reply"
                          长度 ≥ 1
        max_rounds: 上限（防 reroute 死循环）

    Returns:
        ConvergenceResult，rounds = 第几轮达到收敛（≥ 80% confirmed）
    """
    if not broadcast_rounds:
        return ConvergenceResult(converged=False, rounds=0, reason="empty")

    for i, rd in enumerate(broadcast_rounds, start=1):
        total = rd.get("n_confirmed", 0) + rd.get("n_rejected", 0) + rd.get("n_no_reply", 0)
        if total == 0:
            continue
        confirmed_rate = rd["n_confirmed"] / total
        if confirmed_rate >= 0.8:
            return ConvergenceResult(converged=True, rounds=i, reason="consensus")
        if i >= max_rounds:
            return ConvergenceResult(converged=False, rounds=i, reason="max_rounds")

    return ConvergenceResult(converged=False, rounds=len(broadcast_rounds), reason="veto_loop")


# ============================================================
# 自测（python3 -m agents.group_dynamics）
# ============================================================

if __name__ == "__main__":
    # Mock GroupMember 4 人
    members = [
        GroupMember(name="@小张", avatar_emoji="🧑"),
        GroupMember(name="@小李", avatar_emoji="👩"),
        GroupMember(name="@阿明", avatar_emoji="👨"),
        GroupMember(name="@大牛", avatar_emoji="🧓"),
    ]

    # Case A: vetoer — 小李 rejected 3 次
    history_a = {
        "@小张": [ContactResponse(contact="@小张", avatar="🧑", status="confirmed", reply_text="行")],
        "@小李": [
            ContactResponse(contact="@小李", avatar="👩", status="rejected", reply_text="不行", rejection_reason="spicy"),
            ContactResponse(contact="@小李", avatar="👩", status="rejected", reply_text="也不行", rejection_reason="loud"),
            ContactResponse(contact="@小李", avatar="👩", status="rejected", reply_text="还是不行", rejection_reason="other"),
        ],
        "@阿明": [ContactResponse(contact="@阿明", avatar="👨", status="confirmed")],
        "@大牛": [ContactResponse(contact="@大牛", avatar="🧓", status="confirmed")],
    }
    profiles = profile_group(members, history_a, first_responder="@小张")
    assert profiles["@小李"].pattern == "vetoer", profiles["@小李"].pattern
    print(f"✓ Case A vetoer: {profiles['@小李'].evidence}")

    # Case B: silent — 大牛 2 次 no_reply
    history_b = {
        "@小张": [ContactResponse(contact="@小张", avatar="🧑", status="confirmed")],
        "@小李": [ContactResponse(contact="@小李", avatar="👩", status="confirmed")],
        "@阿明": [ContactResponse(contact="@阿明", avatar="👨", status="confirmed")],
        "@大牛": [
            ContactResponse(contact="@大牛", avatar="🧓", status="no_reply"),
            ContactResponse(contact="@大牛", avatar="🧓", status="no_reply"),
        ],
    }
    profiles = profile_group(members, history_b, first_responder="@小张")
    assert profiles["@大牛"].pattern == "silent", profiles["@大牛"].pattern
    print(f"✓ Case B silent: {profiles['@大牛'].evidence}")

    # Case C: implicit_leader — 小张第 1 个回，含"听我的"
    history_c = {
        "@小张": [ContactResponse(contact="@小张", avatar="🧑", status="confirmed",
                                  reply_text="听我的就这家！", reply_at_ms=100)],
        "@小李": [ContactResponse(contact="@小李", avatar="👩", status="confirmed", reply_at_ms=2000)],
        "@阿明": [ContactResponse(contact="@阿明", avatar="👨", status="confirmed", reply_at_ms=3000)],
        "@大牛": [ContactResponse(contact="@大牛", avatar="🧓", status="confirmed", reply_at_ms=4000)],
    }
    profiles = profile_group(members, history_c, first_responder="@小张")
    assert profiles["@小张"].pattern == "implicit_leader", profiles["@小张"].pattern
    assert profiles["@小张"].weight == 1.5
    print(f"✓ Case C implicit_leader: weight={profiles['@小张'].weight}")

    # Case D: 收敛轮次度量
    rounds = [
        {"n_confirmed": 1, "n_rejected": 2, "n_no_reply": 1},  # 第 1 轮 25%
        {"n_confirmed": 3, "n_rejected": 1, "n_no_reply": 0},  # 第 2 轮 75%
        {"n_confirmed": 4, "n_rejected": 0, "n_no_reply": 0},  # 第 3 轮 100%
    ]
    cr = measure_convergence(rounds)
    assert cr.converged and cr.rounds == 3, cr
    print(f"✓ Case D convergence: round={cr.rounds} reason={cr.reason}")

    # Case E: veto_loop（未收敛）
    rounds_loop = [
        {"n_confirmed": 1, "n_rejected": 3, "n_no_reply": 0},
        {"n_confirmed": 2, "n_rejected": 2, "n_no_reply": 0},
        {"n_confirmed": 1, "n_rejected": 3, "n_no_reply": 0},
        {"n_confirmed": 2, "n_rejected": 2, "n_no_reply": 0},
    ]
    cr = measure_convergence(rounds_loop, max_rounds=4)
    assert not cr.converged and cr.reason in ("max_rounds", "veto_loop"), cr
    print(f"✓ Case E veto_loop: round={cr.rounds} reason={cr.reason}")

    print("\n所有自测通过！")
