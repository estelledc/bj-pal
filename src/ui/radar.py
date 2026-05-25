"""reasons 雷达图（v2 改 11）。

不引入 plotly 这种重依赖；用 streamlit 原生绘图（matplotlib 已 pandas 间接装上）。
"""

from __future__ import annotations

import math
from typing import Optional


def render_radar(reasons: list, title: str = ""):
    """5 维雷达图：amap_rating / ugc_soft / budget_fit / distance / crowd_penalty。

    输入 reasons 是 list[Reason]（含 factor + contrib）。
    用 SVG 字符串内嵌（避免 matplotlib 依赖问题）。
    """
    import streamlit as st

    factors = ["amap_rating", "ugc_soft_score", "budget_fit", "distance", "crowd_penalty"]
    factor_labels = ["评分", "UGC", "预算", "距离", "拥堵反向"]
    by_factor = {}
    for r in reasons:
        f = getattr(r, "factor", None) or (r.get("factor") if isinstance(r, dict) else None)
        c = getattr(r, "contrib", None) or (r.get("contrib") if isinstance(r, dict) else 0)
        by_factor[f] = float(c)

    # 归一化到 0-1（contrib 可正可负，正贡献加分，负贡献减分）
    values = []
    for f in factors:
        v = by_factor.get(f, 0)
        # crowd_penalty 是负贡献——我们想要"越不拥堵越大"，所以反转
        if f == "crowd_penalty":
            # 范围 -0.10 到 0：reverse 显示
            v = max(0.0, 0.10 + v) / 0.10
        else:
            # 主要范围 0-0.35（rating） 到 0-0.30（ugc）等；统一映射到 0-1
            v = max(0.0, min(v / 0.35, 1.0))
        values.append(v)

    svg = _build_radar_svg(factor_labels, values, title=title)
    st.markdown(svg, unsafe_allow_html=True)


def _build_radar_svg(labels: list[str], values: list[float], title: str = "",
                      size: int = 240) -> str:
    """生成内嵌 SVG（不依赖 matplotlib / plotly）。"""
    n = len(labels)
    cx, cy = size // 2, size // 2
    radius = size * 0.36
    # 多边形顶点（5 顶点）
    polygon_pts = []
    label_pts = []
    grid_lines = []
    for i, (lbl, val) in enumerate(zip(labels, values)):
        angle = -math.pi / 2 + 2 * math.pi * i / n   # 12 点起，顺时针
        x = cx + radius * val * math.cos(angle)
        y = cy + radius * val * math.sin(angle)
        polygon_pts.append(f"{x:.1f},{y:.1f}")
        # 标签位置（往外稍微偏）
        lx = cx + (radius + 26) * math.cos(angle)
        ly = cy + (radius + 26) * math.sin(angle)
        label_pts.append((lx, ly, lbl, val))
        # 网格线
        gx = cx + radius * math.cos(angle)
        gy = cy + radius * math.sin(angle)
        grid_lines.append(f'<line x1="{cx}" y1="{cy}" x2="{gx:.1f}" y2="{gy:.1f}" stroke="#D5C8B0" stroke-width="0.5"/>')

    # 4 圈刻度
    rings = []
    for level in [0.25, 0.5, 0.75, 1.0]:
        ring_pts = []
        for i in range(n):
            angle = -math.pi / 2 + 2 * math.pi * i / n
            x = cx + radius * level * math.cos(angle)
            y = cy + radius * level * math.sin(angle)
            ring_pts.append(f"{x:.1f},{y:.1f}")
        rings.append(f'<polygon points="{" ".join(ring_pts)}" fill="none" stroke="#E8DEC8" stroke-width="0.6"/>')

    polygon = f'<polygon points="{" ".join(polygon_pts)}" fill="rgba(200, 48, 45, 0.25)" stroke="#C8302D" stroke-width="1.8"/>'

    # 标签 SVG
    label_svg = ""
    for lx, ly, lbl, val in label_pts:
        label_svg += (
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
            f'font-size="11" fill="#2A2A2A" font-family="serif">{lbl} '
            f'<tspan fill="#888">{val:.2f}</tspan></text>'
        )

    title_svg = ""
    if title:
        title_svg = f'<text x="{cx}" y="14" text-anchor="middle" font-size="12" fill="#666">{title}</text>'

    return (
        f'<div style="display:flex;justify-content:center">'
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'{title_svg}'
        f'{"".join(rings)}'
        f'{"".join(grid_lines)}'
        f'{polygon}'
        f'{label_svg}'
        f'</svg></div>'
    )
