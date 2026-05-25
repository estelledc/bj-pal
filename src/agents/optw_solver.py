"""OPTW: Orienteering Problem with Time Windows（[31] 改进点）。

参考论文：
- Vansteenwegen et al. 2011, "The orienteering problem: A survey" (EJOR)
- Verbeeck et al. 2014, "A fast solution method for the OPTW"

问题：N 个候选 POI 各带 (utility, [open, close], visit_duration)，
给定起始时间 + 总时长上限 T，从中挑一个有序子集（access route），
满足：
- 任意 POI 的到达时间 ∈ [open, close - visit_duration]
- 总时长（含通勤）≤ T
- 起点起步、终点终止
最大化：Σ utility (i in selected)

NP-hard。N ≤ 50, T ≤ 360 分钟，OR-Tools CP-SAT 5s timeout 实测能出近似最优。

实现：用 OR-Tools CpModel.AddCircuit([(head, tail, literal)]) 标准 TSP 约束。
- 自循环 (i, i, not_visited[i]) 表示 i 不在 route 上
- 路径 (i, j, edge_ij) 表示从 i 到 j
- AddCircuit 自动保证至少一个 Hamiltonian sub-circuit

时间约束用 IntVar arrival[i] + reified bool 编码 edge：
    edge_ij ⇒ arrival[j] >= arrival[i] + visit_i + travel_ij
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# 数据类型
# ============================================================

@dataclass
class OPTWPoi:
    id: str
    name: str
    utility: int                 # ≥ 0，越大越想去
    open_min: int
    close_min: int
    visit_min: int
    lng: Optional[float] = None
    lat: Optional[float] = None


@dataclass
class OPTWResult:
    sequence: list[str]              # 访问的 POI id 顺序
    arrival_times: list[int]         # 对应到达分钟数
    total_utility: int
    total_minutes_used: int
    n_visited: int
    solver_status: str
    solve_time_s: float
    objective_bound: Optional[int] = None
    extra: dict = field(default_factory=dict)


# ============================================================
# 通勤时间矩阵
# ============================================================

def build_travel_matrix(
    pois: list[OPTWPoi],
    start: tuple[float, float],
    end: Optional[tuple[float, float]] = None,
    walking_kmh: float = 5.0,
    detour_factor: float = 1.3,
) -> list[list[int]]:
    """节点编号：0=起点，1..N=POI，N+1=终点。返回整数（向上取整分钟）。"""
    if end is None:
        end = start
    nodes = [start] + [(p.lng, p.lat) for p in pois] + [end]
    N = len(nodes)
    M = [[0] * N for _ in range(N)]
    for i in range(N):
        for j in range(N):
            if i == j:
                continue
            ai, aj = nodes[i], nodes[j]
            if any(v is None for v in (*ai, *aj)):
                M[i][j] = 30
                continue
            d_km = _haversine_km(ai[0], ai[1], aj[0], aj[1]) * detour_factor
            M[i][j] = max(1, math.ceil(d_km / walking_kmh * 60))
    return M


def _haversine_km(lng1, lat1, lng2, lat2) -> float:
    if any(v is None for v in (lng1, lat1, lng2, lat2)):
        return 0.0
    from math import radians, sin, cos, asin, sqrt
    lng1, lat1, lng2, lat2 = map(radians, [lng1, lat1, lng2, lat2])
    dlng, dlat = lng2 - lng1, lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


# ============================================================
# OPTW solver — 用 AddCircuit 标准建模
# ============================================================

def solve_optw(
    pois: list[OPTWPoi],
    start_min: int,
    end_min: int,
    travel_matrix: list[list[int]],
    min_visits: int = 4,
    max_visits: int = 7,
    time_limit_s: float = 5.0,
) -> OPTWResult:
    """OPTW CP-SAT 求解。"""
    from ortools.sat.python import cp_model

    N = len(pois)
    if N == 0:
        return OPTWResult([], [], 0, 0, 0, "INFEASIBLE", 0.0)

    START = 0
    END = N + 1
    POI_NODES = list(range(1, N + 1))
    M = travel_matrix
    horizon = max(end_min, start_min) + 1

    model = cp_model.CpModel()

    # ---- 变量 ----
    visited = {i: model.NewBoolVar(f"v_{i}") for i in POI_NODES}
    arrival = {i: model.NewIntVar(0, horizon, f"a_{i}") for i in [START] + POI_NODES + [END]}

    # 边变量 edge[i,j] 当且仅当 route 上从 i 直达 j
    # 说明：CP-SAT AddCircuit 要求节点 0..K 必须形成一个 Hamiltonian circuit；
    # 用自循环 (i, i, not_visited[i]) 把不访问的 POI 排除。
    edges: dict[tuple[int, int], "cp_model.IntVar"] = {}
    arcs_list: list[tuple[int, int, "cp_model.IntVar"]] = []

    # 自循环：未访问的 POI（visited=False 时自循环）
    for i in POI_NODES:
        not_visited = visited[i].Not()
        arcs_list.append((i, i, not_visited))
    # 起点 / 终点不允许自循环（必须出边 / 入边）
    # 起点出边到 POI 或直接到终点
    for j in POI_NODES + [END]:
        e = model.NewBoolVar(f"e_{START}_{j}")
        edges[(START, j)] = e
        arcs_list.append((START, j, e))
    # POI 间互通 + POI 到终点
    for i in POI_NODES:
        for j in POI_NODES + [END]:
            if i == j:
                continue
            e = model.NewBoolVar(f"e_{i}_{j}")
            edges[(i, j)] = e
            arcs_list.append((i, j, e))

    # ---- AddCircuit 闭环：加虚边 END → START（不计时间）----
    close_loop = model.NewConstant(1)
    arcs_list.append((END, START, close_loop))

    # ---- AddCircuit 约束（核心）----
    model.AddCircuit(arcs_list)

    # ---- visited 与 incoming edge 关联：j 被访问 ⇔ Σ incoming(j) == 1 ----
    for j in POI_NODES:
        incoming = [edges[(i, j)] for i in [START] + POI_NODES if i != j]
        model.Add(sum(incoming) == visited[j])

    # ---- 出边数 = 入边数（不要求，AddCircuit 已保证） ----

    # ---- 时间传递：edge[i,j] ⇒ arrival[j] >= arrival[i] + visit_i + travel ----
    # 起点：arrival = start_min（固定）
    model.Add(arrival[START] == start_min)
    for (i, j), e in edges.items():
        if i == START:
            visit_i = 0
        else:
            visit_i = pois[i - 1].visit_min
        travel = M[i][j]
        model.Add(arrival[j] >= arrival[i] + visit_i + travel).OnlyEnforceIf(e)

    # ---- 时窗：被访问 POI 的 arrival ∈ [open, close - visit_min] ----
    for idx, p in enumerate(pois, start=1):
        model.Add(arrival[idx] >= p.open_min).OnlyEnforceIf(visited[idx])
        model.Add(arrival[idx] <= p.close_min - p.visit_min).OnlyEnforceIf(visited[idx])

    # ---- 终点 arrival ≤ end_min ----
    model.Add(arrival[END] <= end_min)

    # ---- visit 数量约束 ----
    visits_sum = sum(visited[i] for i in POI_NODES)
    model.Add(visits_sum >= min_visits)
    model.Add(visits_sum <= max_visits)

    # ---- 目标 ----
    model.Maximize(sum(visited[idx] * pois[idx - 1].utility for idx in POI_NODES))

    # ---- 求解 ----
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    # 实测多 worker 在 N=8 模型下反而无返回（疑 reify 约束 + 多 worker 互斥）
    solver.parameters.num_search_workers = 1
    t0 = time.time()
    status = solver.Solve(model)
    solve_t = time.time() - t0

    status_name = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
        cp_model.UNKNOWN: "UNKNOWN",
    }.get(status, f"STATUS_{status}")

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return OPTWResult([], [], 0, 0, 0, status_name, round(solve_t, 2))

    # ---- 提取解 ----
    sequence: list[str] = []
    arrival_times: list[int] = []
    cur = START
    seen = {START}
    safety = N + 5
    while cur != END and safety > 0:
        safety -= 1
        # 找 cur 出边唯一的 j
        nxt = None
        for (i, j), e in edges.items():
            if i == cur and solver.Value(e) == 1:
                nxt = j
                break
        if nxt is None or nxt in seen:
            break
        seen.add(nxt)
        if nxt != END:
            sequence.append(pois[nxt - 1].id)
            arrival_times.append(solver.Value(arrival[nxt]))
        cur = nxt

    total_minutes = solver.Value(arrival[END]) - start_min
    total_utility = int(solver.ObjectiveValue())

    return OPTWResult(
        sequence=sequence,
        arrival_times=arrival_times,
        total_utility=total_utility,
        total_minutes_used=total_minutes,
        n_visited=len(sequence),
        solver_status=status_name,
        solve_time_s=round(solve_t, 2),
        objective_bound=int(solver.BestObjectiveBound()),
    )


# ============================================================
# 辅助：从 RankedPOI 列表组装 OPTW 输入
# ============================================================

def from_ranked_pois(
    ranked,
    target_start: str = "14:00",
    duration_hours: float = 4.5,
    default_visit_min: int = 60,
    default_open: int = 9 * 60,
    default_close: int = 22 * 60,
) -> tuple[list[OPTWPoi], int, int]:
    """从 RankedPOI 列表造 OPTW 输入。score×1000 → utility。"""
    start_min = _hh(target_start) or (14 * 60)
    end_min = start_min + int(duration_hours * 60)

    pois: list[OPTWPoi] = []
    for r in ranked:
        poi = r.poi if hasattr(r, "poi") else r
        utility = int(round(r.score * 1000)) if hasattr(r, "score") else 500
        visit = default_visit_min
        cat = (poi.category_lv2 or "") + (poi.category_lv3 or "")
        if any(kw in cat for kw in ("咖啡", "甜品")):
            visit = 45
        elif any(kw in cat for kw in ("公园", "博物馆", "景点")):
            visit = 90
        open_min, close_min = _parse_open_time(poi.open_time, default_open, default_close)
        pois.append(OPTWPoi(
            id=poi.id, name=poi.name,
            utility=utility,
            open_min=open_min, close_min=close_min,
            visit_min=visit,
            lng=poi.longitude, lat=poi.latitude,
        ))
    return pois, start_min, end_min


def _hh(time_str: str) -> Optional[int]:
    if not time_str:
        return None
    parts = time_str.split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1] if len(parts) > 1 else 0)
    except ValueError:
        return None


def _parse_open_time(s: Optional[str], default_open: int, default_close: int) -> tuple[int, int]:
    if not s:
        return default_open, default_close
    if any(k in s for k in ("全天", "24小时", "00:00-24:00")):
        return 0, 24 * 60 - 1
    import re
    nums = re.findall(r"(\d{1,2}):(\d{2})", s)
    if len(nums) >= 2:
        try:
            o = int(nums[0][0]) * 60 + int(nums[0][1])
            c = int(nums[1][0]) * 60 + int(nums[1][1])
            if c <= o:
                c += 24 * 60
            return o, c
        except ValueError:
            pass
    return default_open, default_close


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    pois = [
        OPTWPoi("P1", "故宫",       utility=900, open_min=510,  close_min=960,  visit_min=120, lng=116.397, lat=39.916),
        OPTWPoi("P2", "南锣鼓巷",   utility=700, open_min=600,  close_min=22*60, visit_min=60,  lng=116.402, lat=39.937),
        OPTWPoi("P3", "雍和宫",     utility=600, open_min=540,  close_min=16*60, visit_min=60,  lng=116.414, lat=39.948),
        OPTWPoi("P4", "簋街胡大",   utility=500, open_min=17*60,close_min=24*60, visit_min=75,  lng=116.426, lat=39.939),
        OPTWPoi("P5", "三联书店",   utility=400, open_min=9*60, close_min=22*60, visit_min=45,  lng=116.418, lat=39.910),
        OPTWPoi("P6", "景山公园",   utility=550, open_min=390,  close_min=22*60, visit_min=60,  lng=116.395, lat=39.927),
    ]
    start_xy = (116.395, 39.916)
    M = build_travel_matrix(pois, start=start_xy)
    print(f"Travel matrix {len(M)}x{len(M[0])}, 起点→故宫={M[0][1]}min")

    # 14:00-18:30 共 270 min。故宫 visit 120 太长，3 步通常不可行；
    # 关掉故宫或选 min_visits=2 / 改 18:30 → 19:30 都可行。
    result = solve_optw(
        pois=pois,
        start_min=14 * 60,
        end_min=18 * 60 + 30,
        travel_matrix=M,
        min_visits=2,
        max_visits=4,
        time_limit_s=5.0,
    )

    print(f"\nstatus={result.solver_status}, solve {result.solve_time_s}s")
    print(f"utility={result.total_utility}, n_visited={result.n_visited}")
    print(f"total_min_used={result.total_minutes_used} / spec=270")
    print("访问顺序：")
    name_by_id = {p.id: p.name for p in pois}
    for pid, t in zip(result.sequence, result.arrival_times):
        print(f"  {t // 60:02d}:{t % 60:02d}  {pid} {name_by_id[pid]}")
