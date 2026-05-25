"""[20] 受众分层测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.audience_segment import (
    AUDIENCE_KEYWORDS, AudienceProfile,
    build_index, get_profile,
    get_audience_score_adjust,
    get_top_local_secrets, get_top_tourist_landmarks,
)


# ============================================================
# 关键词
# ============================================================

def test_keywords_present():
    assert "本地" in AUDIENCE_KEYWORDS["local"]
    assert "打卡" in AUDIENCE_KEYWORDS["tourist"]
    assert "深度" in AUDIENCE_KEYWORDS["expert"]


# ============================================================
# Profile 数据类
# ============================================================

def test_profile_label_clear_local():
    p = AudienceProfile(poi_name="X", local_count=5, tourist_count=1)
    assert p.label() == "local"


def test_profile_label_mixed():
    p = AudienceProfile(poi_name="X", local_count=3, tourist_count=2)
    assert p.label(threshold=2) == "mixed"


def test_profile_label_unknown():
    p = AudienceProfile(poi_name="X")
    assert p.label() == "unknown"


def test_local_secret_threshold():
    p = AudienceProfile(poi_name="X", local_count=3, tourist_count=1)
    assert p.is_local_secret() is True


def test_tourist_must_go_threshold():
    p = AudienceProfile(poi_name="X", local_count=1, tourist_count=4)
    assert p.is_tourist_must_go() is True


# ============================================================
# 索引
# ============================================================

def test_build_index():
    n = build_index()
    assert n >= 100  # 至少 100 个 POI 有 audience signal


def test_yonghegong_is_tourist():
    """雍和宫 UGC 多次提到打卡 / 必去 / 排队 → tourist 主导。"""
    p = get_profile("雍和宫")
    assert p is not None
    assert p.tourist_count > p.local_count


def test_get_top_local_secrets():
    top = get_top_local_secrets(top_k=5)
    assert len(top) >= 1
    for p in top:
        assert p.is_local_secret()


def test_get_top_tourist_landmarks():
    top = get_top_tourist_landmarks(top_k=5)
    assert len(top) >= 3
    for p in top:
        assert p.is_tourist_must_go()


# ============================================================
# 加分 / 减分
# ============================================================

def test_score_adjust_tourist_pref_for_landmark():
    """偏好 tourist + 雍和宫（tourist landmark）→ +0.10。"""
    delta, why = get_audience_score_adjust("雍和宫", preference="tourist")
    assert delta > 0
    assert "必去" in why or "网红" in why or "打卡" in why or "地标" in why


def test_score_adjust_local_pref_demotes_landmark():
    """偏好 local + 雍和宫（tourist 多）→ 减分。"""
    delta, _ = get_audience_score_adjust("雍和宫", preference="local")
    assert delta < 0


def test_score_adjust_no_preference():
    delta, why = get_audience_score_adjust("雍和宫", preference=None)
    assert delta == 0.0
    assert why == ""


def test_score_adjust_unknown_poi():
    delta, _ = get_audience_score_adjust("某神秘小店", preference="local")
    assert delta == 0.0


# ============================================================
# rank_fuse 集成
# ============================================================

def test_fuse_with_audience_preference():
    """rank_fuse 加 audience_preference='local'，雍和宫被减分。"""
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    p = POI(id="P_YHG", name="雍和宫", category_lv1="风景名胜",
            category_lv2="风景名胜", category_lv3="寺庙",
            typecode="", district="", business_area="", address="",
            longitude=116.414, latitude=39.948, rating=4.6, avg_price=25,
            open_time="09:00-16:00", phone="", photos=[])
    c = SearchConstraints(persona="solo", min_rating=4.0)

    ranked_no = fuse_and_rank([p], c)
    ranked_local = fuse_and_rank([p], c, audience_preference="local")
    ranked_tourist = fuse_and_rank([p], c, audience_preference="tourist")

    s_no = ranked_no[0].score
    s_local = ranked_local[0].score
    s_tourist = ranked_tourist[0].score

    # local 视角下应被减分；tourist 视角下应被加分
    assert s_local < s_no
    assert s_tourist > s_no


def test_fuse_audience_off_no_reason():
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    p = POI(id="P_YHG", name="雍和宫", category_lv1="风景名胜",
            category_lv2="风景名胜", category_lv3="寺庙",
            typecode="", district="", business_area="", address="",
            longitude=116.414, latitude=39.948, rating=4.6, avg_price=25,
            open_time="09:00-16:00", phone="", photos=[])
    c = SearchConstraints(persona="solo", min_rating=4.0)
    ranked = fuse_and_rank([p], c)  # 默认 audience_preference=None
    has_a = any(rs.factor.startswith("audience_") for r in ranked for rs in r.reasons)
    assert has_a is False


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
