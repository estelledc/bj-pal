"""[47][48] Borda + Kemeny 群投票算法测试。

覆盖：
- 一致偏好 → 共识第一名稳定
- 多数 vs 少数 → Kemeny 不让多数完全压死少数（看 pair）
- Condorcet 循环 → 算法不挂，给出最优分歧
- 边界：Kendall tau 距离对极端排序
- 集成：group_rank(aggregate_by='kemeny')
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.voting import (
    borda_count, borda_ranking,
    kendall_tau_distance,
    kemeny_consensus, kemeny_score,
    group_consensus,
)


def test_borda_unanimous():
    rankings = [["A", "B", "C"], ["A", "B", "C"], ["A", "B", "C"]]
    s = borda_count(rankings)
    assert s["A"] == 6  # 每人 2 分 × 3
    assert s["B"] == 3
    assert s["C"] == 0
    assert borda_ranking(rankings) == ["A", "B", "C"]


def test_borda_split_majority():
    """2 人 A>B>C，1 人 C>B>A → A 胜（pluralistic）。"""
    rankings = [["A", "B", "C"], ["A", "B", "C"], ["C", "B", "A"]]
    rank = borda_ranking(rankings)
    assert rank[0] == "A"


def test_kendall_tau_identity():
    assert kendall_tau_distance(["X", "Y", "Z"], ["X", "Y", "Z"]) == 0


def test_kendall_tau_max():
    """完全反向 K=3 → C(K,2)=3 swaps。"""
    assert kendall_tau_distance(["A", "B", "C"], ["C", "B", "A"]) == 3


def test_kemeny_unanimous():
    rankings = [["A", "B", "C"], ["A", "B", "C"], ["A", "B", "C"]]
    cons = kemeny_consensus(rankings)
    assert cons == ["A", "B", "C"]
    assert kemeny_score(cons, rankings) == 0


def test_kemeny_avoids_majority_tyranny():
    """Kemeny 不会让多数 100% 压死少数 — 共识考虑 pair 偏好结构。

    Case: 2 人 A>B>C，1 人 C>A>B（少数党也喜欢 A）
    Kemeny 应给 A>B>C 或 A>C>B，不会把 C 压最后（因为少数党偏好被纳入）。
    """
    rankings = [["A", "B", "C"], ["A", "B", "C"], ["C", "A", "B"]]
    cons = kemeny_consensus(rankings)
    assert cons[0] == "A"  # A 是 Condorcet 赢家


def test_kemeny_condorcet_cycle():
    """Condorcet 循环不会挂（NP-hard 但 K=3 暴力可解）。"""
    rankings = [["A", "B", "C"], ["B", "C", "A"], ["C", "A", "B"]]
    cons = kemeny_consensus(rankings)
    assert len(cons) == 3
    # K=3 循环最优分歧 = 4
    assert kemeny_score(cons, rankings) == 4


def test_kemeny_fallback_borda_for_large_K():
    """K > 7 时回退 Borda（NP-hard 暴力枚举 8! = 40320 即将超时）。"""
    rankings = [
        list("ABCDEFGHI"),
        list("BCDEFGHIA"),
    ]
    cons = kemeny_consensus(rankings)
    # 应等于 Borda 排序
    assert cons == borda_ranking(rankings)


def test_group_consensus_two_stage():
    """两段聚合 = Borda 粗排取 top-K + Kemeny 精排。"""
    rankings = [
        ["故宫", "南锣", "三里屯", "国贸", "簋街", "簋街二", "其他1", "其他2"],
        ["故宫", "三里屯", "南锣", "簋街", "国贸", "其他1", "其他2", "簋街二"],
        ["三里屯", "国贸", "故宫", "南锣", "簋街", "其他1", "其他2", "簋街二"],
        ["南锣", "故宫", "三里屯", "国贸", "簋街", "其他1", "其他2", "簋街二"],
    ]
    out = group_consensus(rankings, coarse_top=5)
    assert out["n_voters"] == 4
    assert out["n_candidates_total"] == 8
    assert out["n_candidates_kemeny"] == 5
    assert len(out["kemeny_consensus"]) == 5
    assert "故宫" in out["kemeny_consensus"][:2]  # 故宫是 Condorcet 赢家


def test_kendall_tau_set_mismatch_raises():
    try:
        kendall_tau_distance(["A", "B"], ["A", "C"])
        assert False, "应该抛 ValueError"
    except ValueError:
        pass


def test_group_rank_kemeny_integration():
    """与 group_harmony.group_rank 集成（aggregate_by='kemeny'）。"""
    from tools.types import POI
    from tools.mock_message import GroupMember
    from agents.group_harmony import group_rank

    members = [
        GroupMember(name="@小李", diet_aversion=[], prefers=["coffee"]),
        GroupMember(name="@老周", diet_aversion=[], prefers=["meat"]),
    ]
    candidates = [
        POI(id="P1", name="咖啡馆", category_lv1="咖啡", category_lv2="咖啡馆",
            category_lv3="咖啡", typecode="", district="", business_area="",
            address="", longitude=0, latitude=0, rating=4.5, avg_price=40,
            open_time="", phone="", photos=[]),
        POI(id="P2", name="烤鸭店", category_lv1="餐饮", category_lv2="北京菜",
            category_lv3="北京菜", typecode="", district="", business_area="",
            address="", longitude=0, latitude=0, rating=4.5, avg_price=300,
            open_time="", phone="", photos=[]),
    ]
    result = group_rank(candidates, members, aggregate_by="kemeny")
    assert len(result) == 2
    # Kemeny mode 必有 kemeny_consensus_rank reason
    has_kemeny_reason = any(
        rs.factor == "kemeny_consensus_rank"
        for r in result for rs in r.reasons
    )
    assert has_kemeny_reason


if __name__ == "__main__":
    # 简单 runner
    import inspect
    fns = [f for n, f in globals().items() if n.startswith("test_") and inspect.isfunction(f)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"✓ {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"✗ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} 通过")
    sys.exit(failed)
