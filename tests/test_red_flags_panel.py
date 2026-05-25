"""P0.1 验收：red flags 吐槽面板。

- Aspect 加 evidence_age_days / source_count / decayed_confidence / dataset_version
- freshness_decay 按类目衰减
- extract_red_flags 给 UI 拉一条最关键吐槽
- 5 家有 negative 的 POI 必显示原文 + 日期
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.ugc_signals import (  # noqa: E402
    _compute_age_days,
    extract_red_flags,
    fetch_aspects,
    freshness_decay,
)


def t1_freshness_decay_curve():
    """半衰期模型：food 30 天 → 0.5；scenic 90 天 → 0.5。"""
    food_30 = freshness_decay(30, "food")
    food_90 = freshness_decay(90, "food")
    scenic_90 = freshness_decay(90, "scenic")
    culture_180 = freshness_decay(180, "culture")
    print(f"\n[1] food/30d={food_30}  food/90d={food_90}  scenic/90d={scenic_90}  culture/180d={culture_180}")
    assert 0.45 <= food_30 <= 0.55
    assert food_90 < food_30
    assert 0.45 <= scenic_90 <= 0.55
    assert 0.45 <= culture_180 <= 0.55


def t2_aspect_carries_age_and_dataset():
    """fetch_aspects 返回的 Aspect 都应有 evidence_age_days 和 dataset_version。"""
    aspects = fetch_aspects(area_anchor="五道营-雍和宫片区")
    assert aspects, "应有 UGC 数据"
    print(f"\n[2] 五道营 共 {len(aspects)} 条 aspects")
    print(f"    dataset_versions: {set(a.dataset_version for a in aspects[:10])}")
    print(f"    age_days range: {min(a.evidence_age_days for a in aspects)}-{max(a.evidence_age_days for a in aspects)}")
    print(f"    decayed_conf range: {min(a.decayed_confidence for a in aspects):.2f}-{max(a.decayed_confidence for a in aspects):.2f}")
    assert all(a.dataset_version for a in aspects), "所有 aspect 必有 dataset_version"
    assert all(a.evidence_age_days >= 0 for a in aspects)
    assert all(0 <= a.decayed_confidence <= a.confidence + 0.01 for a in aspects)
    return len(aspects)


def t3_red_flags_for_5_pois():
    """随便挑 5 家有 negative aspect 的 POI，extract_red_flags 必返回 1 条吐槽。"""
    # 先找含 negative 的 poi_name 列表
    all_aspects = fetch_aspects()
    poi_with_neg = sorted({a.poi_name for a in all_aspects
                            if a.sentiment == "negative" and a.poi_name})[:8]
    print(f"\n[3] 抽 8 家测试，找 5 家能出 red flag：")
    hit_count = 0
    for poi in poi_with_neg:
        flags = extract_red_flags(poi_name=poi, top_k=1)
        if flags:
            hit_count += 1
            f = flags[0]
            print(f"    ⚠ {poi:25} [{f['aspect_type']:10}] "
                  f"conf={f['confidence']:.2f}/decay={f['decayed_confidence']:.2f} "
                  f"age={f['age_days']}d src={f['source_count']} "
                  f"conflict={f['conflicting_signals']} dim={f['should_dim']}")
            print(f"      原文: {f['evidence_summary'][:70]}")
            assert f["evidence_summary"], "必须有原文"
            assert f["age_days"] >= 0
            assert f["aspect_type"]
        if hit_count >= 5:
            break
    assert hit_count >= 5, f"应至少 5 家能出 red flag，实际 {hit_count}"
    return hit_count


def t4_should_dim_when_conf_low_or_old():
    """confidence < 0.5 或 age > 30 → should_dim=True。"""
    # 找一条老数据（age > 30）
    all_aspects = fetch_aspects()
    old_neg = [a for a in all_aspects
               if a.sentiment == "negative" and a.evidence_age_days > 30]
    if not old_neg:
        print(f"\n[4] 没有 age>30 的 negative aspect，跳过")
        return 0
    poi = old_neg[0].poi_name
    flags = extract_red_flags(poi_name=poi)
    print(f"\n[4] {poi} red flag:")
    if flags:
        print(f"    age={flags[0]['age_days']}d should_dim={flags[0]['should_dim']}")
        if flags[0]["age_days"] > 30:
            assert flags[0]["should_dim"], "age>30 应标灰"
    return len(flags)


def t5_no_red_flag_when_no_negative():
    """全正向 POI 不出 red flag。"""
    flags = extract_red_flags(poi_name="不存在的 POI")
    assert flags == []
    return True


def t6_age_days_consistency():
    """同一 dataset_version 不同 aspect 的 age_days 应一致。"""
    aspects = fetch_aspects()
    by_ver: dict = {}
    for a in aspects:
        by_ver.setdefault(a.dataset_version, []).append(a.evidence_age_days)
    print(f"\n[6] dataset_version → age_days 一致性：")
    for ver, ages in by_ver.items():
        unique = set(ages)
        # 每天跑测试 age_days 都会 +1，所以只检查同一组只有 1 个值
        print(f"    {ver:50}  unique_ages={len(unique)}  first={list(unique)[0] if unique else '-'}")
        assert len(unique) <= 1, f"{ver} age_days 不一致：{unique}"
    return len(by_ver)


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal P0.1 Red Flags 吐槽面板 Tests")
    print("=" * 60)
    suite = [
        ("freshness_decay", t1_freshness_decay_curve),
        ("aspect_carries_age", t2_aspect_carries_age_and_dataset),
        ("red_flags_5_pois", t3_red_flags_for_5_pois),
        ("should_dim_old", t4_should_dim_when_conf_low_or_old),
        ("empty_when_no_neg", t5_no_red_flag_when_no_negative),
        ("age_consistency", t6_age_days_consistency),
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
    print("✓ P0.1 验收 OK")
