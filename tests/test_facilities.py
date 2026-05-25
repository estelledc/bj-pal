"""[01] facility 字段抽取测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.facilities import (
    FacilityProfile,
    FACILITY_KEYWORDS,
    build_index, get_profile,
    get_facility_score_adjust, filter_by_constraints,
)


# ============================================================
# 关键词库
# ============================================================

def test_keywords():
    assert "母婴室" in FACILITY_KEYWORDS["baby"]
    assert "厕所" in FACILITY_KEYWORDS["toilet"]
    assert "无障碍" in FACILITY_KEYWORDS["wheelchair"]
    assert "停车" in FACILITY_KEYWORDS["parking"]


# ============================================================
# 索引
# ============================================================

def test_build_index():
    n = build_index()
    assert n > 1000


def test_profile_dataclass():
    p = FacilityProfile(poi_name="X", baby=1, toilet=0, wheelchair=-1)
    assert p.is_kid_friendly() is True
    assert p.is_wheelchair_friendly() is False
    assert p.has_blocker_for("wheelchair") is True


# ============================================================
# 真实 POI
# ============================================================

def test_chaoyang_dyc_is_kid_friendly():
    """朝阳大悦城 UGC 提到母婴室齐全 → baby=+1."""
    prof = get_profile("朝阳大悦城")
    assert prof is not None
    assert prof.baby >= 1
    assert prof.parking >= 1  # 也有停车位


def test_sanlitun_negative_signals():
    """三里屯 UGC 提到停车困难 + 不适合带娃 → 多个 -1."""
    prof = get_profile("三里屯太古里")
    assert prof is not None
    assert prof.parking <= -1


def test_unknown_poi_is_none():
    assert get_profile("某神秘小店") is None


def test_score_adjust_kid():
    """带娃用户去朝阳大悦城 → 加分；去三里屯 → 减分。"""
    delta_dyc, _ = get_facility_score_adjust("朝阳大悦城", has_child=True)
    delta_slt, _ = get_facility_score_adjust("三里屯太古里", has_child=True)
    assert delta_dyc > 0
    assert delta_slt <= 0


def test_score_adjust_no_constraint():
    """没约束时 facility 不参与 → delta=0。"""
    delta, reasons = get_facility_score_adjust("朝阳大悦城",
                                               has_child=False,
                                               wheelchair=False, driving=False)
    assert delta == 0.0
    assert reasons == []


def test_filter_by_kid_constraint():
    class P:
        def __init__(self, name, rating=4.5):
            self.name = name
            self.rating = rating
    pool = [
        P("朝阳大悦城"),     # baby+
        P("三里屯太古里"),   # baby- → 应被过滤
        P("某神秘咖啡馆"),    # 无数据 → 放行
    ]
    out = filter_by_constraints(pool, has_child=True, child_age=3)
    names = [p.name for p in out]
    assert "朝阳大悦城" in names
    assert "三里屯太古里" not in names
    assert "某神秘咖啡馆" in names  # 无数据放行


# ============================================================
# rank_fuse 集成
# ============================================================

def test_fuse_and_rank_facility_aware():
    """带娃约束时，朝阳大悦城应被加分（baby+），三里屯应被减分（baby-）。"""
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    pois = [
        POI(id="P1", name="朝阳大悦城", category_lv1="购物服务",
            category_lv2="商场", category_lv3="商场",
            typecode="", district="", business_area="", address="",
            longitude=116.49, latitude=39.92, rating=4.5, avg_price=200,
            open_time="10:00-22:00", phone="", photos=[]),
        POI(id="P2", name="三里屯太古里", category_lv1="购物服务",
            category_lv2="商场", category_lv3="商场",
            typecode="", district="", business_area="", address="",
            longitude=116.45, latitude=39.94, rating=4.5, avg_price=200,
            open_time="10:00-22:00", phone="", photos=[]),
    ]
    c_kid = SearchConstraints(persona="family", min_rating=4.0, has_child=True, child_age=3)
    c_no = SearchConstraints(persona="family", min_rating=4.0, has_child=False)

    ranked_kid = fuse_and_rank(pois, c_kid, facility_aware=True)
    ranked_no = fuse_and_rank(pois, c_no, facility_aware=True)

    # 带娃模式下 dyc 应该比同条件下不带娃模式 score 更高
    s_dyc_kid = next(r.score for r in ranked_kid if r.poi.id == "P1")
    s_dyc_no = next(r.score for r in ranked_no if r.poi.id == "P1")
    assert s_dyc_kid > s_dyc_no

    # 三里屯反过来：带娃模式下因 baby- 应被减分
    s_slt_kid = next(r.score for r in ranked_kid if r.poi.id == "P2")
    s_slt_no = next(r.score for r in ranked_no if r.poi.id == "P2")
    assert s_slt_kid < s_slt_no


def test_fuse_and_rank_facility_off():
    """facility_aware=False 时不加 facility reason。"""
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    p = POI(id="P1", name="朝阳大悦城", category_lv1="购物服务",
            category_lv2="商场", category_lv3="商场",
            typecode="", district="", business_area="", address="",
            longitude=116.49, latitude=39.92, rating=4.5, avg_price=200,
            open_time="10:00-22:00", phone="", photos=[])
    c = SearchConstraints(persona="family", min_rating=4.0, has_child=True, child_age=3)
    ranked = fuse_and_rank([p], c, facility_aware=False)
    has_facility = any(rs.factor.startswith("facility")
                       for r in ranked for rs in r.reasons)
    assert has_facility is False


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
