"""[08] 老字号识别 + ranking 集成测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.heritage_brand import (
    identify_brand,
    score_branch_quality,
    is_heritage_brand_query,
    filter_heritage_pois,
    list_known_brands,
    _load,
)


# ============================================================
# 加载 + 识别
# ============================================================

def test_load_brands():
    brands = _load()
    assert len(brands) >= 18  # 至少 18 个老字号
    assert "全聚德" in brands
    assert "稻香村" in brands


def test_identify_branch_vs_flagship():
    """全聚德(南锣鼓巷店) → 分店；全聚德前门店 → 总店。"""
    branch = identify_brand("全聚德(南锣鼓巷店)")
    flagship = identify_brand("全聚德前门店")
    assert branch is not None and branch.brand == "全聚德"
    assert flagship is not None and flagship.brand == "全聚德"
    assert branch.is_flagship is False
    assert flagship.is_flagship is True


def test_identify_unknown():
    assert identify_brand("某神秘咖啡馆") is None
    assert identify_brand("") is None


def test_identify_no_suffix_means_flagship():
    """无 (xx店) 后缀 → 视为总店（如 "东来顺东小馆清真砂锅"）。"""
    info = identify_brand("北京三禾稻香村")
    assert info is not None
    assert info.is_flagship is True


def test_identify_via_flagship_keyword():
    """名称含 '总店' / '老店' → flagship。"""
    info = identify_brand("张一元茶庄(总店)")
    assert info is not None and info.is_flagship is True


def test_identify_via_flagship_location():
    """名称含 flagship_locations 任一 → flagship。"""
    info = identify_brand("吴裕泰茶庄(王府井店)")
    assert info is not None and info.is_flagship is True


def test_score_branch_quality_flagship():
    class FakePoi:
        name = "全聚德前门店"
        rating = 4.6
    assert score_branch_quality(FakePoi()) == 1.0


def test_score_branch_quality_non_brand():
    class FakePoi:
        name = "某未知店"
        rating = 4.5
    assert score_branch_quality(FakePoi()) == 0.5


def test_score_branch_quality_subpar():
    class FakePoi:
        name = "全聚德(差评分店)"
        rating = 3.5
    s = score_branch_quality(FakePoi())
    assert 0.2 <= s <= 0.4


# ============================================================
# Query detection
# ============================================================

def test_query_detection():
    assert is_heritage_brand_query("想吃老字号烤鸭") is True
    assert is_heritage_brand_query("找个百年老店") is True
    assert is_heritage_brand_query("地道京味") is True
    assert is_heritage_brand_query("现代咖啡馆") is False
    assert is_heritage_brand_query("") is False


# ============================================================
# Filter heritage_pois
# ============================================================

def test_filter_heritage_only_flagship():
    class P:
        def __init__(self, name, rating=4.5):
            self.name = name
            self.rating = rating
    pool = [
        P("全聚德前门店", 4.7),
        P("全聚德(南锣鼓巷店)", 4.3),
        P("某咖啡馆", 4.5),
        P("东来顺饭庄(前门大街店)", 4.7),
    ]
    out = filter_heritage_pois(pool, only_flagship=True)
    names = [p.name for p in out]
    assert "全聚德前门店" in names
    assert "全聚德(南锣鼓巷店)" not in names
    assert "某咖啡馆" not in names
    assert "东来顺饭庄(前门大街店)" in names


# ============================================================
# rank_fuse 集成
# ============================================================

def test_fuse_and_rank_heritage_query_adjusts_scores():
    """heritage_query=True 时，flagship 触发 ×1.15，非老字号 ×0.85。

    两份 ranked 同一 POI 的 score 比较，验证 boost / demote 方向。
    """
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    candidates = [
        POI(id="P1", name="全聚德前门店", category_lv1="餐饮服务",
            category_lv2="北京菜", category_lv3="烤鸭",
            typecode="", district="", business_area="", address="",
            longitude=116.39, latitude=39.90, rating=4.6, avg_price=200,
            open_time="11:00-22:00", phone="", photos=[]),
        POI(id="P2", name="某网红烤鸭", category_lv1="餐饮服务",
            category_lv2="北京菜", category_lv3="烤鸭",
            typecode="", district="", business_area="", address="",
            longitude=116.39, latitude=39.90, rating=4.6, avg_price=200,
            open_time="11:00-22:00", phone="", photos=[]),
    ]
    constraints = SearchConstraints(persona="family", min_rating=4.0)

    ranked_normal = fuse_and_rank(candidates, constraints, heritage_query=False)
    ranked_heritage = fuse_and_rank(candidates, constraints, heritage_query=True)

    # 同一 POI 的 score 比较：全聚德应该 boost（×1.15），某网红应该 demote（×0.85）
    qjd_n = next(r.score for r in ranked_normal if r.poi.id == "P1")
    qjd_h = next(r.score for r in ranked_heritage if r.poi.id == "P1")
    other_n = next(r.score for r in ranked_normal if r.poi.id == "P2")
    other_h = next(r.score for r in ranked_heritage if r.poi.id == "P2")

    assert qjd_h > qjd_n, f"flagship 应被 boost: {qjd_n} → {qjd_h}"
    assert other_h < other_n, f"非老字号在 heritage_query 下应被 demote: {other_n} → {other_h}"

    # 全聚德必有 flagship reason
    qjd_reasons = next(r.reasons for r in ranked_heritage if r.poi.id == "P1")
    assert any(rs.factor == "heritage_brand_flagship" for rs in qjd_reasons)


def test_fuse_and_rank_heritage_subpar_branch_demoted():
    """非总店且评分低于品牌底线 → score ×0.7。"""
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    sub = POI(id="P1", name="全聚德(某偏远分店)", category_lv1="餐饮服务",
              category_lv2="北京菜", category_lv3="烤鸭",
              typecode="", district="", business_area="", address="",
              longitude=116.39, latitude=39.90, rating=3.5, avg_price=200,
              open_time="11:00-22:00", phone="", photos=[])
    constraints = SearchConstraints(persona="family", min_rating=3.0)
    ranked = fuse_and_rank([sub], constraints, heritage_query=True)
    # 应该有 heritage_brand_branch_subpar reason
    has_subpar = any(rs.factor == "heritage_brand_branch_subpar"
                     for r in ranked for rs in r.reasons)
    assert has_subpar


if __name__ == "__main__":
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
