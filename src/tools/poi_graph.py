"""POI 实体图（[22] GraphRAG 简化版）。

不调 LLM 也能建图。三种边来源：

1. **co_mention**：同一条 UGC 的 evidence_summary 里同时被提到 → 共现关系
   （现实意义：UGC 作者把这两 POI 放一起讨论 = 业务关联）
2. **same_area**：amap area_anchor 相同 → 同片区候选
3. **geographic_neighbor**：haversine ≤ 0.5km → 步行可达邻居

聚合后用 networkx：
- PageRank 算 POI 全局重要性（业务热度）
- find_neighbors(poi)：图上 1-hop 邻居，按 edge weight 倒序，作为 reroute 候选
- find_complementary(poi, kind)：1-hop 邻居里筛 kind 不同 → 餐厅后接咖啡馆

集成：
- replanner：reroute 时优先选 find_neighbors(failed_poi) 而非全片区扫
- planner：plan_optw 候选池增强（拉重要 POI 优先）
"""

from __future__ import annotations

import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class POINode:
    poi_id: str
    name: str
    area_anchor: str = ""
    category_lv1: str = ""
    category_lv2: str = ""
    longitude: float = 0.0
    latitude: float = 0.0
    pagerank: float = 0.0


@dataclass
class POIEdge:
    src: str
    dst: str
    weight: float
    edge_types: list[str] = field(default_factory=list)


_GRAPH = None              # nx.Graph
_NODES: dict[str, POINode] = {}
_NAME_TO_ID: dict[str, str] = {}


# ============================================================
# 建图
# ============================================================

def build_graph(force_rebuild: bool = False, max_nodes: int = 5000) -> int:
    """构建 POI 图。返回节点数。"""
    global _GRAPH, _NODES, _NAME_TO_ID
    if _GRAPH is not None and not force_rebuild:
        return _GRAPH.number_of_nodes()

    import networkx as nx
    from loader import get_conn

    conn = get_conn()
    # 1) 节点：所有 amap POI（限制 top-N 按评分）
    rows = conn.execute(
        "SELECT id, name, business_area, district, category_lv1, category_lv2, "
        "longitude, latitude, rating "
        "FROM pois "
        "WHERE longitude IS NOT NULL "
        "ORDER BY rating DESC LIMIT ?",
        (max_nodes,)
    ).fetchall()
    nodes: dict[str, POINode] = {}
    name_to_id: dict[str, str] = {}
    for r in rows:
        # 用 business_area + district 拼 area_anchor 替代字段
        area = r["business_area"] or r["district"] or ""
        n = POINode(
            poi_id=r["id"], name=r["name"] or "",
            area_anchor=area,
            category_lv1=r["category_lv1"] or "",
            category_lv2=r["category_lv2"] or "",
            longitude=r["longitude"] or 0.0,
            latitude=r["latitude"] or 0.0,
        )
        nodes[r["id"]] = n
        if n.name:
            name_to_id[n.name] = r["id"]

    G = nx.Graph()
    for nid, n in nodes.items():
        G.add_node(nid, **n.__dict__)

    # 2) co_mention 边：同一条 UGC evidence 提到 ≥ 2 个 POI
    ugc_rows = conn.execute(
        "SELECT evidence_summary FROM ugc_aspects WHERE evidence_summary IS NOT NULL"
    ).fetchall()
    co_mention_pairs: Counter = Counter()
    for r in ugc_rows:
        txt = r["evidence_summary"]
        # 简单匹配：扫所有 POI 名是否在 txt 中
        # 为避免 O(N×M)，只扫 name 长度 ≥ 3 的
        mentioned = []
        for name, nid in name_to_id.items():
            if len(name) >= 3 and name in txt:
                mentioned.append(nid)
                if len(mentioned) >= 5:
                    break
        if len(mentioned) >= 2:
            for i in range(len(mentioned)):
                for j in range(i + 1, len(mentioned)):
                    a, b = sorted([mentioned[i], mentioned[j]])
                    co_mention_pairs[(a, b)] += 1
    conn.close()

    n_co = 0
    for (a, b), w in co_mention_pairs.items():
        if a == b:
            continue
        G.add_edge(a, b, weight=w * 2.0, types=["co_mention"])
        n_co += 1

    # 3) same_area 边：同 area_anchor 的 POI 互连（仅 top-rated 的，避免爆炸）
    area_groups: dict[str, list[str]] = {}
    for nid, n in nodes.items():
        if n.area_anchor:
            area_groups.setdefault(n.area_anchor, []).append(nid)
    n_area = 0
    for area, ids in area_groups.items():
        if len(ids) > 30:
            ids = ids[:30]   # 取前 30 个（按 rating 已排）
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if G.has_edge(a, b):
                    G[a][b]["weight"] += 0.5
                    if "same_area" not in G[a][b]["types"]:
                        G[a][b]["types"].append("same_area")
                else:
                    G.add_edge(a, b, weight=0.5, types=["same_area"])
                    n_area += 1

    # 4) geographic_neighbor 边：距离 ≤ 0.5km
    n_geo = _add_geo_edges(G, nodes, threshold_km=0.5)

    # 5) PageRank
    pr = nx.pagerank(G, alpha=0.85, max_iter=100)
    for nid, score in pr.items():
        nodes[nid].pagerank = score

    _GRAPH = G
    _NODES = nodes
    _NAME_TO_ID = name_to_id
    logger.info(
        f"[poi_graph] 建图: {G.number_of_nodes()} 节点, "
        f"{G.number_of_edges()} 边 (co_mention={n_co} same_area={n_area} geo={n_geo})"
    )
    return G.number_of_nodes()


def _add_geo_edges(G, nodes: dict[str, POINode], threshold_km: float) -> int:
    """空间索引：把 POI 按 lng/lat grid 分桶，仅同桶 + 邻桶之间算距离。"""
    from math import floor
    grid_size = 0.005  # ~500m
    buckets: dict[tuple[int, int], list[str]] = {}
    for nid, n in nodes.items():
        if n.longitude == 0 or n.latitude == 0:
            continue
        key = (floor(n.longitude / grid_size), floor(n.latitude / grid_size))
        buckets.setdefault(key, []).append(nid)

    n_added = 0
    for (gx, gy), ids in buckets.items():
        # 同桶 + 邻桶
        candidate_ids = list(ids)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if (dx, dy) == (0, 0):
                    continue
                candidate_ids.extend(buckets.get((gx + dx, gy + dy), []))
        for i, a in enumerate(ids):
            na = nodes[a]
            for b in candidate_ids:
                if a >= b:
                    continue
                nb = nodes[b]
                if nb.longitude == 0 or nb.latitude == 0:
                    continue
                d = _haversine(na.longitude, na.latitude, nb.longitude, nb.latitude)
                if d > threshold_km:
                    continue
                w = max(0.1, 1.0 - d / threshold_km)  # 越近权重越高
                if G.has_edge(a, b):
                    G[a][b]["weight"] += w
                    if "geo" not in G[a][b]["types"]:
                        G[a][b]["types"].append("geo")
                else:
                    G.add_edge(a, b, weight=w, types=["geo"])
                    n_added += 1
    return n_added


def _haversine(lng1, lat1, lng2, lat2) -> float:
    from math import radians, sin, cos, asin, sqrt
    if any(v is None or v == 0 for v in (lng1, lat1, lng2, lat2)):
        return 999
    lng1, lat1, lng2, lat2 = map(radians, [lng1, lat1, lng2, lat2])
    dlng, dlat = lng2 - lng1, lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


# ============================================================
# 查询接口
# ============================================================

def find_neighbors(
    poi_id_or_name: str,
    top_k: int = 8,
    same_category_only: bool = False,
) -> list[tuple[str, float, list[str]]]:
    """图上 1-hop 邻居，按 edge weight × neighbor_pagerank 倒序。

    Returns:
        [(neighbor_poi_id, score, edge_types), ...]
    """
    if _GRAPH is None:
        build_graph()
    pid = _resolve(poi_id_or_name)
    if pid is None or pid not in _GRAPH:
        return []

    src_cat = _NODES[pid].category_lv1
    out = []
    for nbr in _GRAPH.neighbors(pid):
        if nbr == pid:
            continue
        if same_category_only and _NODES[nbr].category_lv1 != src_cat:
            continue
        edge = _GRAPH[pid][nbr]
        score = edge["weight"] * (1 + _NODES[nbr].pagerank * 100)
        out.append((nbr, round(score, 4), list(edge["types"])))
    out.sort(key=lambda x: x[1], reverse=True)
    return out[:top_k]


def find_complementary(
    poi_id_or_name: str,
    target_kind: str,
    top_k: int = 5,
) -> list[tuple[str, float, list[str]]]:
    """1-hop 邻居里筛 category_lv1 == target_kind（如餐厅后接咖啡馆）。"""
    if _GRAPH is None:
        build_graph()
    pid = _resolve(poi_id_or_name)
    if pid is None or pid not in _GRAPH:
        return []
    out = []
    for nbr in _GRAPH.neighbors(pid):
        if _NODES[nbr].category_lv1 != target_kind:
            continue
        edge = _GRAPH[pid][nbr]
        score = edge["weight"] * (1 + _NODES[nbr].pagerank * 100)
        out.append((nbr, round(score, 4), list(edge["types"])))
    out.sort(key=lambda x: x[1], reverse=True)
    return out[:top_k]


def get_top_pois_by_pagerank(top_k: int = 20, area_anchor: Optional[str] = None) -> list[POINode]:
    if _GRAPH is None:
        build_graph()
    pool = _NODES.values()
    if area_anchor:
        pool = [n for n in pool if n.area_anchor == area_anchor]
    return sorted(pool, key=lambda n: n.pagerank, reverse=True)[:top_k]


def get_node(poi_id_or_name: str) -> Optional[POINode]:
    if _GRAPH is None:
        build_graph()
    pid = _resolve(poi_id_or_name)
    return _NODES.get(pid) if pid else None


def _resolve(poi_id_or_name: str) -> Optional[str]:
    """name 或 id 解析为 id。"""
    if not poi_id_or_name:
        return None
    if poi_id_or_name in _NODES:
        return poi_id_or_name
    if poi_id_or_name in _NAME_TO_ID:
        return _NAME_TO_ID[poi_id_or_name]
    # 模糊匹配
    for name, nid in _NAME_TO_ID.items():
        if poi_id_or_name in name or name in poi_id_or_name:
            return nid
    return None


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    import time
    t0 = time.time()
    n = build_graph()
    print(f"图构建: {n} 节点 in {time.time()-t0:.2f}s")
    print(f"边数: {_GRAPH.number_of_edges()}")

    # PageRank top
    print("\n=== PageRank Top 10 ===")
    for n in get_top_pois_by_pagerank(top_k=10):
        print(f"  pr={n.pagerank:.5f}  {n.name:25s}  area={n.area_anchor}")

    # 单查邻居
    print("\n=== 故宫 / 雍和宫 / 三里屯太古里 邻居 ===")
    for q in ["故宫博物院", "雍和宫", "三里屯太古里"]:
        node = get_node(q)
        if not node:
            print(f"\n  {q}: 找不到")
            continue
        print(f"\n  [{q}] cat={node.category_lv1} area={node.area_anchor}")
        for nbr_id, score, types in find_neighbors(q, top_k=5):
            nb = _NODES[nbr_id]
            print(f"    score={score:.2f}  {nb.name:25s} ({nb.category_lv1}) types={types}")

    # 互补查询
    print("\n=== 故宫 → 餐饮服务 邻居（饭后选项）===")
    for nbr_id, score, types in find_complementary("故宫博物院", "餐饮服务", top_k=5):
        nb = _NODES[nbr_id]
        print(f"  score={score:.2f}  {nb.name:25s} types={types}")
