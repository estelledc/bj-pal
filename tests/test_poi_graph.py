"""[22] POI 实体图测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.poi_graph import (
    build_graph, find_neighbors, find_complementary,
    get_top_pois_by_pagerank, get_node, _resolve,
)


# ============================================================
# 建图
# ============================================================

def test_build_graph_size():
    n = build_graph()
    assert n >= 1000  # 至少 1k POI


def test_resolve_by_name():
    pid = _resolve("故宫博物院")
    assert pid is not None
    pid_unknown = _resolve("根本不存在的店")
    assert pid_unknown is None


def test_get_node():
    n = get_node("故宫博物院")
    assert n is not None
    assert "故宫" in n.name
    assert n.pagerank > 0


# ============================================================
# 邻居查询
# ============================================================

def test_find_neighbors_yonghegong():
    """雍和宫 邻居应含国子监（同片区 + 共现）。"""
    nbrs = find_neighbors("雍和宫", top_k=10)
    assert len(nbrs) > 0
    nbr_names = []
    for nbr_id, _, _ in nbrs:
        n = get_node(nbr_id)
        if n:
            nbr_names.append(n.name)
    # 国子监 / 地坛公园 / 五道营胡同 / 周边餐厅之一应该出现
    has_known = any("国子监" in name or "地坛" in name or "五道营" in name
                    for name in nbr_names)
    assert has_known


def test_find_neighbors_returns_sorted():
    """neighbors 按 score 倒序。"""
    nbrs = find_neighbors("雍和宫", top_k=5)
    if len(nbrs) >= 2:
        scores = [s for _, s, _ in nbrs]
        for i in range(1, len(scores)):
            assert scores[i] <= scores[i-1]


def test_find_neighbors_unknown():
    nbrs = find_neighbors("不存在的POI", top_k=5)
    assert nbrs == []


def test_find_complementary_post_meal_coffee():
    """故宫 → 餐饮服务 互补：应给出附近的餐厅。"""
    nbrs = find_complementary("故宫博物院", "餐饮服务", top_k=5)
    # 故宫附近真的有东来顺/烤鸭店等
    assert len(nbrs) >= 1
    for nbr_id, _, _ in nbrs:
        n = get_node(nbr_id)
        assert n.category_lv1 == "餐饮服务"


# ============================================================
# PageRank
# ============================================================

def test_pagerank_top_includes_landmarks():
    """颐和园 / 雍和宫 / 故宫 / 三里屯 等应该在 top 50 里。"""
    top = get_top_pois_by_pagerank(top_k=50)
    names = [n.name for n in top]
    landmarks_found = sum(1 for n in names
                          if any(kw in n for kw in
                                 ["颐和园", "雍和宫", "故宫", "三里屯", "南锣", "长城"]))
    assert landmarks_found >= 3


def test_pagerank_sorted():
    top = get_top_pois_by_pagerank(top_k=10)
    for i in range(1, len(top)):
        assert top[i].pagerank <= top[i-1].pagerank


# ============================================================
# rank_fuse 集成
# ============================================================

def test_fuse_with_graph_anchor():
    """带 graph_anchor 时，anchor 的邻居应被 boost。"""
    from tools.rank_fuse import fuse_and_rank
    from tools.types import POI, SearchConstraints

    # 真实 POI 名 — 雍和宫的邻居（国子监）
    gnz = get_node("国子监")
    yhg = get_node("雍和宫")
    if not (gnz and yhg):
        return  # 数据不齐就跳过

    # 模拟 candidates 含 国子监 + 一个无关 POI
    p1 = POI(id=gnz.poi_id, name=gnz.name, category_lv1=gnz.category_lv1,
             category_lv2=gnz.category_lv2, category_lv3="",
             typecode="", district="", business_area="", address="",
             longitude=gnz.longitude, latitude=gnz.latitude,
             rating=4.5, avg_price=30, open_time="", phone="", photos=[])
    p_other = POI(id="P_FAKE_FAR", name="远方某店", category_lv1="餐饮服务",
                  category_lv2="中餐厅", category_lv3="",
                  typecode="", district="", business_area="", address="",
                  longitude=116.6, latitude=40.0,
                  rating=4.5, avg_price=30, open_time="", phone="", photos=[])
    c = SearchConstraints(persona="family", min_rating=4.0)

    ranked_no = fuse_and_rank([p1, p_other], c)
    ranked_anchored = fuse_and_rank([p1, p_other], c, graph_anchor="雍和宫")

    # 国子监在 anchored 模式下应有 graph_neighbor_of_anchor reason
    p1_reasons = next(r.reasons for r in ranked_anchored if r.poi.id == p1.id)
    has_graph_reason = any(rs.factor == "graph_neighbor_of_anchor" for rs in p1_reasons)
    assert has_graph_reason


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
