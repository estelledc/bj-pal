"""社会选择理论 · 群投票聚合算法（[47][48] 改进点）。

参考：
- Brandt et al. 2016, Handbook of Computational Social Choice
- Bartholdi-Tovey-Trick 1989, 计算社会选择的 NP-hard 性证明
- Kemeny 1959 / Young 1995, 多数共识理论

实现：
- `borda_count`         - O(NK) 粗排，每人第 i 位候选得 K-1-i 分
- `kendall_tau_distance` - O(K²) 两个排序的 swap 距离
- `kemeny_consensus`    - 最优共识：最小化 Σ Kendall tau；K≤7 暴力 K!，K>7 回退 Borda
- `group_consensus`     - 两段聚合：先 Borda 粗排 top-7，再 Kemeny 精排

为什么不直接 min×0.5+avg×0.5 加权法？
- 加权法基于绝对分值，对"高分但极少数人讨厌"的 POI 区分力弱
- 排序聚合保留偏好结构（A 比 B 好），不依赖跨人分数可比
- Kemeny 在 social choice axioms 下是唯一同时满足 Condorcet + 反演中性 + 一致性的方法
"""

from __future__ import annotations

from itertools import permutations
from typing import Sequence


# ============================================================
# Borda Count
# ============================================================

def borda_count(rankings: list[list[str]]) -> dict[str, float]:
    """Borda count: 每人排第 i 位的候选得 K-1-i 分（最爱得 K-1 分）。

    Args:
        rankings: N 人各自对 K 个候选的偏好排序（最爱在前）

    Returns:
        {candidate_id: score}，分数越高越受欢迎。

    复杂度 O(NK)。
    """
    if not rankings:
        return {}
    canon = list(dict.fromkeys(c for r in rankings for c in r))
    K = len(canon)
    scores = {c: 0.0 for c in canon}
    for r in rankings:
        for i, c in enumerate(r):
            scores[c] += K - 1 - i
    return scores


def borda_ranking(rankings: list[list[str]]) -> list[str]:
    """Borda 总排序（按分数降序）。"""
    s = borda_count(rankings)
    return sorted(s.keys(), key=lambda c: (-s[c], c))


# ============================================================
# Kendall tau distance（排序间 swap 距离）
# ============================================================

def kendall_tau_distance(a: Sequence[str], b: Sequence[str]) -> int:
    """两个排序的 swap 距离 — 多少个 pair 顺序不一致。

    Args:
        a, b: 同一候选集的两个排序

    Returns:
        距离 ∈ [0, K(K-1)/2]，0 = 完全相同。

    复杂度 O(K²)。
    """
    if set(a) != set(b):
        raise ValueError("kendall_tau_distance: 两个排序元素集必须相同")
    pos_b = {c: i for i, c in enumerate(b)}
    K = len(a)
    dist = 0
    for i in range(K):
        for j in range(i + 1, K):
            if pos_b[a[i]] > pos_b[a[j]]:
                dist += 1
    return dist


# ============================================================
# Kemeny consensus
# ============================================================

def kemeny_consensus(rankings: list[list[str]]) -> list[str]:
    """Kemeny-Young 最优共识：最小化 Σ_i Kendall tau(consensus, ranking_i)。

    Args:
        rankings: N 人各自对 K 个候选的排序

    Returns:
        最优共识排序

    实现：
    - K ≤ 7：暴力枚举 K! 排列（K=5 → 120 permutations，K=7 → 5040）
    - K > 7：回退 Borda（NP-hard 整数规划留作 [47] 重型版本）

    复杂度 O(K! · NK²)。
    """
    if not rankings:
        return []
    canon = list(dict.fromkeys(c for r in rankings for c in r))
    K = len(canon)

    canon_set = set(canon)
    for r in rankings:
        if set(r) != canon_set:
            raise ValueError(
                f"kemeny_consensus: 所有 ranking 必须含同样的候选集。"
                f"差异：缺 {canon_set - set(r)}，多 {set(r) - canon_set}"
            )

    if K > 7:
        # 暴力枚举不现实，回退 Borda 或留待 ILP 实现
        return borda_ranking(rankings)

    best_order: tuple[str, ...] = tuple(canon)
    best_total = float("inf")
    for perm in permutations(canon):
        total = sum(kendall_tau_distance(perm, r) for r in rankings)
        if total < best_total:
            best_total = total
            best_order = perm
    return list(best_order)


def kemeny_score(consensus: Sequence[str], rankings: list[list[str]]) -> int:
    """共识排序对所有人的总 swap 距离（衡量分歧度）。"""
    return sum(kendall_tau_distance(consensus, r) for r in rankings)


# ============================================================
# 两段聚合：Borda 粗排 → Kemeny 精排
# ============================================================

def group_consensus(
    rankings: list[list[str]],
    coarse_top: int = 7,
) -> dict:
    """两段聚合：先 Borda 粗排取 top-K，再 Kemeny 精排。

    Args:
        rankings: N 人各自对所有候选的排序
        coarse_top: 粗排后取前几个进 Kemeny 精排（建议 ≤ 7）

    Returns:
        {
            "borda_scores": {c: score},
            "borda_top": [c1, c2, ...],
            "kemeny_consensus": [c1, c2, ...],
            "kemeny_disagreement": int,  # 总 swap 距离
            "n_voters": N,
            "n_candidates_total": K,
            "n_candidates_kemeny": min(coarse_top, K),
        }
    """
    if not rankings:
        return {"borda_scores": {}, "borda_top": [], "kemeny_consensus": [],
                "kemeny_disagreement": 0, "n_voters": 0,
                "n_candidates_total": 0, "n_candidates_kemeny": 0}

    n_voters = len(rankings)
    bs = borda_count(rankings)
    K_total = len(bs)
    K_keep = min(coarse_top, K_total)
    borda_top = sorted(bs.keys(), key=lambda c: (-bs[c], c))[:K_keep]

    # 把每人的 ranking 限制到 borda_top（保留原相对顺序）
    keep_set = set(borda_top)
    sub_rankings = [
        [c for c in r if c in keep_set] for r in rankings
    ]
    # 万一有人没投到 borda_top 中的某个，补到末尾
    for r in sub_rankings:
        for c in borda_top:
            if c not in r:
                r.append(c)

    consensus = kemeny_consensus(sub_rankings)
    return {
        "borda_scores": {k: round(v, 2) for k, v in bs.items()},
        "borda_top": borda_top,
        "kemeny_consensus": consensus,
        "kemeny_disagreement": kemeny_score(consensus, sub_rankings),
        "n_voters": n_voters,
        "n_candidates_total": K_total,
        "n_candidates_kemeny": K_keep,
    }


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    # Case 1: 简单一致 — 3 人都把 A 排第一
    rankings = [
        ["A", "B", "C", "D"],
        ["A", "C", "B", "D"],
        ["A", "B", "D", "C"],
    ]
    out = group_consensus(rankings, coarse_top=4)
    assert out["kemeny_consensus"][0] == "A", "A 应该是共识第一"
    print(f"✓ Case 1 一致: {out['kemeny_consensus']} disagreement={out['kemeny_disagreement']}")

    # Case 2: 多数暴政陷阱 — 3 人都讨厌 D，但 2 人最爱 A 1 人最爱 B
    rankings = [
        ["A", "B", "C", "D"],
        ["A", "C", "B", "D"],
        ["B", "C", "A", "D"],
    ]
    out = group_consensus(rankings, coarse_top=4)
    print(f"✓ Case 2 多数: {out['kemeny_consensus']} disagreement={out['kemeny_disagreement']}")

    # Case 3: Condorcet 循环 — A>B, B>C, C>A 各 2:1
    rankings = [
        ["A", "B", "C"],
        ["B", "C", "A"],
        ["C", "A", "B"],
    ]
    out = group_consensus(rankings, coarse_top=3)
    # K=3 Condorcet 循环：3 人 × 3 个 pair = 9 个 pair-disagreement 总量；
    # 最优共识每个 pair 至少违背 1 人，最优 disagreement = 3 + 1 = 4（手算可证）
    assert out["kemeny_disagreement"] == 4, f"Condorcet K=3 disagreement 应为 4，实际 {out['kemeny_disagreement']}"
    print(f"✓ Case 3 Condorcet 循环: {out['kemeny_consensus']} disagreement={out['kemeny_disagreement']}")

    # Case 4: kendall_tau_distance 边界
    assert kendall_tau_distance(["A", "B", "C"], ["A", "B", "C"]) == 0
    assert kendall_tau_distance(["A", "B", "C"], ["C", "B", "A"]) == 3
    print("✓ Case 4 Kendall tau 边界 OK")

    # Case 5: 5 候选 4 人 — 演示场景规模
    rankings = [
        ["故宫", "南锣", "三里屯", "国贸", "簋街"],
        ["故宫", "三里屯", "南锣", "簋街", "国贸"],
        ["三里屯", "国贸", "故宫", "南锣", "簋街"],
        ["南锣", "故宫", "三里屯", "国贸", "簋街"],
    ]
    out = group_consensus(rankings, coarse_top=5)
    print(f"✓ Case 5 真实规模: 共识={out['kemeny_consensus']} 分歧={out['kemeny_disagreement']}")
    print(f"  Borda 分数: {out['borda_scores']}")

    print("\n所有自测通过！")
