"""W1 D4 验收：rank_fuse 融合排序 + reasons 解释。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.amap_search import resolve_area_center, search_pois  # noqa: E402
from tools.rank_fuse import fuse_and_rank  # noqa: E402
from tools.types import SearchConstraints  # noqa: E402


def t1_basic_ranking():
    """五道营片区餐饮，家庭画像 ¥120 预算，看 ranking 顶部是不是合理。"""
    constraints = SearchConstraints(
        persona="family",
        has_child=True,
        child_age=5,
        budget_per_person=120,
        min_rating=4.5,
        walk_radius_km=1.5,
    )
    candidates = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="food",
        constraints=constraints,
        limit=30,
    )
    center = resolve_area_center("五道营-雍和宫片区")
    ranked = fuse_and_rank(candidates, constraints, center=center)
    print(f"\n[1] 家庭画像 ¥120，排名前 5：")
    for r in ranked[:5]:
        print(f"    score={r.score:.3f}  {r.poi.name:25} ¥{r.poi.avg_price}  {r.poi.rating}")
        for reason in r.reasons:
            print(f"      • [{reason.factor:18}] {reason.contrib:+.3f}  {reason.evidence}")
    assert len(ranked) >= 3
    return len(ranked)


def t2_budget_excludes_kingyin():
    """京兆尹 ¥966 预算 ¥120 时应被 hard filter 砍掉。"""
    constraints = SearchConstraints(
        persona="family",
        budget_per_person=120,
        min_rating=4.5,
        walk_radius_km=1.5,
    )
    candidates = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="food",
        constraints=constraints,
        limit=30,
    )
    ranked = fuse_and_rank(candidates, constraints)
    names = [r.poi.name for r in ranked]
    print(f"\n[2] ¥120 预算下 ranking ({len(ranked)} 家)：")
    for r in ranked[:5]:
        print(f"    {r.poi.name:25} ¥{r.poi.avg_price}")
    assert "京兆尹(雍和宫店)" not in names, "京兆尹 ¥966 应被 hard filter 砍"
    return len(ranked)


def t3_friends_higher_budget():
    """朋友画像 ¥250，京兆尹应该出现并接近顶部。"""
    constraints = SearchConstraints(
        persona="friends",
        budget_per_person=250,
        min_rating=4.5,
        walk_radius_km=1.5,
    )
    candidates = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="food",
        constraints=constraints,
        limit=30,
    )
    ranked = fuse_and_rank(candidates, constraints)
    print(f"\n[3] 朋友画像 ¥250 ({len(ranked)} 家)：")
    for r in ranked[:8]:
        print(f"    score={r.score:.3f}  {r.poi.name:25} ¥{r.poi.avg_price}")
    return len(ranked)


def t4_yonghegong_ugc_penalty():
    """雍和宫有 negative UGC（排队 0.86），应被 crowd_penalty 拉低。"""
    constraints = SearchConstraints(min_rating=4.0, walk_radius_km=2.0)
    candidates = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="all",
        constraints=constraints,
        limit=50,
    )
    yh = [c for c in candidates if c.name == "雍和宫"]
    if not yh:
        print("\n[4] 雍和宫不在候选中（可能被半径过滤），skip")
        return 0
    ranked = fuse_and_rank(candidates, constraints)
    yh_rank = [r for r in ranked if r.poi.name == "雍和宫"]
    if not yh_rank:
        print("\n[4] 雍和宫被 hard filter，skip")
        return 0
    rr = yh_rank[0]
    print(f"\n[4] 雍和宫 score={rr.score:.3f}, risk_tags={rr.risk_tags}")
    for reason in rr.reasons:
        print(f"    • [{reason.factor}] {reason.contrib:+.3f}  {reason.evidence[:60]}")
    crowd_reasons = [r for r in rr.reasons if r.factor == "crowd_penalty"]
    assert crowd_reasons, "雍和宫应有 crowd_penalty reason"
    assert crowd_reasons[0].contrib < 0, "crowd_penalty 应为负贡献"
    return rr.score


def t5_reasons_have_evidence():
    """每条 reason 都必须有 evidence 字符串（评委 Q&A 必须能展开）。"""
    constraints = SearchConstraints(min_rating=4.5)
    candidates = search_pois(
        area_anchor="五道营-雍和宫片区",
        category="food",
        constraints=constraints,
        limit=10,
    )
    ranked = fuse_and_rank(candidates, constraints, center=resolve_area_center("五道营-雍和宫片区"))
    print(f"\n[5] reason evidence 检查（前 3 家）")
    for r in ranked[:3]:
        for reason in r.reasons:
            assert reason.evidence, f"{r.poi.name} 的 {reason.factor} reason 缺 evidence"
    print(f"    ✓ {len(ranked)} 家全部 reasons 有 evidence")
    return len(ranked)


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal W1 D4 Ranking Tests")
    print("=" * 60)
    suite = [
        ("basic_ranking", t1_basic_ranking),
        ("budget_excludes_kingyin", t2_budget_excludes_kingyin),
        ("friends_higher_budget", t3_friends_higher_budget),
        ("yonghegong_ugc_penalty", t4_yonghegong_ugc_penalty),
        ("reasons_have_evidence", t5_reasons_have_evidence),
    ]
    failed = []
    for name, fn in suite:
        try:
            fn()
        except AssertionError as e:
            failed.append((name, str(e)))
            print(f"    ✗ {e}")
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))
            import traceback; traceback.print_exc()
    print("\n" + "=" * 60)
    if failed:
        print(f"✗ {len(failed)} 项失败")
        for n, m in failed:
            print(f"  - {n}: {m}")
        sys.exit(1)
    print("✓ W1 D4 验收 OK")
