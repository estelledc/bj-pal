"""v2.4 D5 验收：群偏好收敛器 — 检测三类成员模式 + 收敛轮次度量。

参考：docs/V2.4_ITERATION_PLAN.md
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.group_dynamics import (  # noqa: E402
    classify_member,
    measure_convergence,
    profile_group,
)
from agents.group_harmony import group_rank  # noqa: E402
from tools.amap_search import search_pois  # noqa: E402
from tools.mock_message import (  # noqa: E402
    DEMO_FRIEND_GROUP,
    ContactResponse,
    GroupMember,
)
from tools.types import SearchConstraints  # noqa: E402


def t1_vetoer_pattern_detected():
    """rejected ≥ 2 → vetoer + weight 0.5。"""
    member = GroupMember(name="@小李", avatar_emoji="👩")
    history = [
        ContactResponse(contact="@小李", avatar="👩", status="rejected",
                        reply_text="不行", rejection_reason="spicy"),
        ContactResponse(contact="@小李", avatar="👩", status="rejected",
                        reply_text="也不行", rejection_reason="loud"),
    ]
    p = classify_member(member, history)
    print(f"\n[1] vetoer: pattern={p.pattern} weight={p.weight} reasons={p.evidence['rejection_reasons']}")
    assert p.pattern == "vetoer"
    assert p.weight == 0.5


def t2_silent_pattern_detected():
    """no_reply ≥ 2 → silent + weight 0.7。"""
    member = GroupMember(name="@大牛")
    history = [
        ContactResponse(contact="@大牛", avatar="🧓", status="no_reply"),
        ContactResponse(contact="@大牛", avatar="🧓", status="no_reply"),
    ]
    p = classify_member(member, history)
    print(f"[2] silent: pattern={p.pattern} weight={p.weight}")
    assert p.pattern == "silent"
    assert p.weight == 0.7


def t3_implicit_leader_detected():
    """第 1 个回 + 含 leader phrase + confirmed → implicit_leader + weight 1.5。"""
    member = GroupMember(name="@小张")
    history = [
        ContactResponse(contact="@小张", avatar="🧑", status="confirmed",
                        reply_text="听我的就这家！", reply_at_ms=100),
    ]
    p = classify_member(member, history, broadcast_seq_index_first=True)
    print(f"[3] implicit_leader: pattern={p.pattern} weight={p.weight}")
    assert p.pattern == "implicit_leader"
    assert p.weight == 1.5


def t4_normal_default():
    """normal pattern → weight 1.0。"""
    member = GroupMember(name="@阿明")
    history = [
        ContactResponse(contact="@阿明", avatar="👨", status="confirmed", reply_text="行"),
    ]
    p = classify_member(member, history)
    print(f"[4] normal: pattern={p.pattern} weight={p.weight}")
    assert p.pattern == "normal"
    assert p.weight == 1.0


def t5_convergence_measure_consensus():
    """收敛 = ≥ 80% confirmed。"""
    rounds = [
        {"n_confirmed": 1, "n_rejected": 2, "n_no_reply": 1},  # 25%
        {"n_confirmed": 3, "n_rejected": 1, "n_no_reply": 0},  # 75%
        {"n_confirmed": 4, "n_rejected": 0, "n_no_reply": 0},  # 100%
    ]
    cr = measure_convergence(rounds)
    print(f"[5] convergence: round={cr.rounds} reason={cr.reason}")
    assert cr.converged
    assert cr.rounds == 3


def t6_convergence_within_target():
    """v2.4 度量目标：4 人群 reroute 中位数 ≤ 2 轮。

    模拟 5 个群，3 个 2 轮收敛，1 个 1 轮，1 个 3 轮 → 中位数 2。
    """
    scenarios = [
        # 1 轮收敛
        [{"n_confirmed": 4, "n_rejected": 0, "n_no_reply": 0}],
        # 2 轮收敛 × 3
        [{"n_confirmed": 2, "n_rejected": 2, "n_no_reply": 0},
         {"n_confirmed": 4, "n_rejected": 0, "n_no_reply": 0}],
        [{"n_confirmed": 2, "n_rejected": 1, "n_no_reply": 1},
         {"n_confirmed": 4, "n_rejected": 0, "n_no_reply": 0}],
        [{"n_confirmed": 3, "n_rejected": 1, "n_no_reply": 0},
         {"n_confirmed": 4, "n_rejected": 0, "n_no_reply": 0}],
        # 3 轮收敛
        [{"n_confirmed": 1, "n_rejected": 2, "n_no_reply": 1},
         {"n_confirmed": 3, "n_rejected": 1, "n_no_reply": 0},
         {"n_confirmed": 4, "n_rejected": 0, "n_no_reply": 0}],
    ]
    rounds_to_converge = sorted(measure_convergence(s).rounds for s in scenarios)
    median = rounds_to_converge[len(rounds_to_converge) // 2]
    print(f"[6] 5 场景收敛轮数: {rounds_to_converge}  中位数={median}")
    assert median <= 2, f"v2.4 目标 ≤ 2 轮，实际中位数 {median}"


def t7_group_rank_with_member_weights():
    """group_rank 接 D5 weights 应稳定不崩。"""
    constraints = SearchConstraints(persona="friends", min_rating=4.5,
                                     walk_radius_km=2.0, budget_per_person=300)
    candidates = search_pois(area_anchor="五道营-雍和宫片区", category="food",
                              constraints=constraints, limit=20)
    weights = {"@小张": 1.5, "@小李": 0.7, "@阿明": 1.0, "@大牛": 1.0}
    ranked = group_rank(candidates, DEMO_FRIEND_GROUP, constraints,
                        member_weights=weights)
    print(f"[7] weighted group_rank: {len(ranked)} 通过 top1={ranked[0].poi.name}")
    assert len(ranked) > 0
    # weights reasons 应含权重明细（factor 保持 group_avg_score 向后兼容）
    weighted_reason = next((r for r in ranked[0].reasons
                            if r.factor == "group_avg_score"), None)
    assert weighted_reason is not None
    assert "权重" in weighted_reason.evidence


def t8_profile_group_e2e():
    """4 人群一次跑通 profile_group（用真实 DEMO_FRIEND_GROUP 名字：@小张/@阿明/@小雅/@老王）。"""
    history_by_member = {
        "@小张": [ContactResponse(contact="@小张", avatar="🧑", status="confirmed",
                                  reply_text="听我的就这家！", reply_at_ms=100)],
        "@小雅": [ContactResponse(contact="@小雅", avatar="👩", status="rejected",
                                  rejection_reason="spicy"),
                  ContactResponse(contact="@小雅", avatar="👩", status="rejected",
                                  rejection_reason="loud")],
        "@阿明": [ContactResponse(contact="@阿明", avatar="👨", status="confirmed")],
        "@老王": [ContactResponse(contact="@老王", avatar="🧓", status="no_reply"),
                  ContactResponse(contact="@老王", avatar="🧓", status="no_reply")],
    }
    profiles = profile_group(DEMO_FRIEND_GROUP, history_by_member,
                             first_responder="@小张")
    print(f"[8] e2e profiles:")
    for name, p in profiles.items():
        print(f"     {name:6} {p.pattern:18} w={p.weight}")
    assert profiles["@小张"].pattern == "implicit_leader"
    assert profiles["@小雅"].pattern == "vetoer"
    assert profiles["@老王"].pattern == "silent"
    assert profiles["@阿明"].pattern == "normal"


if __name__ == "__main__":
    t1_vetoer_pattern_detected()
    t2_silent_pattern_detected()
    t3_implicit_leader_detected()
    t4_normal_default()
    t5_convergence_measure_consensus()
    t6_convergence_within_target()
    t7_group_rank_with_member_weights()
    t8_profile_group_e2e()
    print("\n所有 D5 验收通过！")
