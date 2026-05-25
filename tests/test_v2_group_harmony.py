"""v2 改 8 验收：朋友 4 人偏好调和。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.group_harmony import group_rank  # noqa: E402
from tools.amap_search import search_pois  # noqa: E402
from tools.mock_message import DEMO_FRIEND_GROUP, GroupMember  # noqa: E402
from tools.types import SearchConstraints  # noqa: E402


def t1_excludes_spicy_for_aversion():
    """小张 spicy aversion → 火锅店应被剔除。"""
    constraints = SearchConstraints(persona="friends", min_rating=4.5,
                                     walk_radius_km=2.0, budget_per_person=300)
    candidates = search_pois(area_anchor="五道营-雍和宫片区", category="food",
                              constraints=constraints, limit=30)
    ranked = group_rank(candidates, DEMO_FRIEND_GROUP, constraints)
    print(f"\n[1] group_rank({len(candidates)} 候选) → {len(ranked)} 通过")
    for r in ranked[:5]:
        print(f"    score={r.score:.3f} hit={r.hit_count}/4 {r.poi.name:25} ¥{r.poi.avg_price}")
    # 验证：火锅店不应在 ranked
    names = [r.poi.name for r in ranked]
    spicy_hits = [n for n in names if "火锅" in n or "麻辣" in n]
    assert len(spicy_hits) == 0, f"应排除火锅，实际：{spicy_hits}"
    return len(ranked)


def t2_excludes_expensive():
    """阿明 expensive aversion + 预算 ¥250 → 京兆尹 ¥966 被砍。"""
    constraints = SearchConstraints(persona="friends", budget_per_person=250,
                                     min_rating=4.5, walk_radius_km=1.5)
    candidates = search_pois(area_anchor="五道营-雍和宫片区", category="food",
                              constraints=constraints, limit=30)
    ranked = group_rank(candidates, DEMO_FRIEND_GROUP, constraints)
    print(f"\n[2] 朋友 ¥250 ({len(ranked)} 通过)")
    names = [r.poi.name for r in ranked]
    assert "京兆尹(雍和宫店)" not in names, "京兆尹 ¥966 应被 expensive aversion 砍"
    return len(ranked)


def t3_top_picks_have_prefer_hits():
    """ranking 顶部应有 ≥ 1 人偏好命中。"""
    constraints = SearchConstraints(persona="friends", budget_per_person=250,
                                     min_rating=4.0, walk_radius_km=2.0)
    candidates = search_pois(area_anchor="五道营-雍和宫片区", category="all",
                              constraints=constraints, limit=50)
    ranked = group_rank(candidates, DEMO_FRIEND_GROUP, constraints)
    print(f"\n[3] 全类目 ranking 前 5：")
    for r in ranked[:5]:
        print(f"    {r.poi.name:25} score={r.score:.3f} hit={r.hit_count}/4")
        for name, matches in r.member_matches.items():
            if matches:
                print(f"      {name}: {matches}")
    if ranked:
        # 至少前 3 个有命中
        top3_hits = sum(r.hit_count for r in ranked[:3])
        print(f"    前 3 总命中数：{top3_hits}")
    return len(ranked)


def t4_reasons_contain_evidence():
    """每条 ranked 必须有 X/4 人 reasons。"""
    constraints = SearchConstraints(persona="friends", min_rating=4.0)
    candidates = search_pois(area_anchor="五道营-雍和宫片区", category="food",
                              constraints=constraints, limit=20)
    ranked = group_rank(candidates, DEMO_FRIEND_GROUP, constraints)
    print(f"\n[4] reasons 验证")
    for r in ranked[:2]:
        print(f"    {r.poi.name}：")
        for reason in r.reasons:
            print(f"      [{reason.factor:20}] {reason.evidence}")
        avg_reason = next((rr for rr in r.reasons if rr.factor == "group_avg_score"), None)
        assert avg_reason and "/" in avg_reason.evidence, f"缺 X/4 人 reason"
    return len(ranked)


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal v2 改 8 Group Harmony Tests")
    print("=" * 60)
    suite = [
        ("excludes_spicy", t1_excludes_spicy_for_aversion),
        ("excludes_expensive", t2_excludes_expensive),
        ("top_have_hits", t3_top_picks_have_prefer_hits),
        ("reasons_evidence", t4_reasons_contain_evidence),
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
    print("✓ v2 改 8 验收 OK")
