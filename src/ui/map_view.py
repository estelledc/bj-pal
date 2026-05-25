"""folium 地图：plan 中各 POI 的 marker + 步行 polyline。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import folium
import streamlit as st
from streamlit_folium import st_folium

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import get_conn  # noqa: E402
from agents.types import Plan  # noqa: E402

KIND_COLOR = {
    "citywalk": "blue", "meal": "red", "culture": "purple",
    "rest": "orange", "shopping": "green", "snack": "lightred",
    "depart": "gray",
}


def render_map(plan: Plan, center: Optional[tuple[float, float]] = None):
    """渲染 plan 上各 POI 的标记和连线。"""
    poi_coords = _resolve_coords([s.poi_id for s in plan.steps if s.poi_id])
    if not center and poi_coords:
        # 用第一个 POI 作 center
        first = next((c for c in poi_coords.values() if c), None)
        center = first or (116.4166, 39.9474)
    if not center:
        center = (116.4166, 39.9474)
    # folium 用 (lat, lng) 而非 (lng, lat)
    m = folium.Map(location=[center[1], center[0]], zoom_start=15, control_scale=True)

    polyline_pts: list[tuple[float, float]] = []
    for s in plan.steps:
        if not s.poi_id or s.kind == "depart":
            continue
        coords = poi_coords.get(s.poi_id)
        if not coords:
            continue
        lng, lat = coords
        polyline_pts.append((lat, lng))
        color = "darkred" if s.is_rerouted else KIND_COLOR.get(s.kind, "blue")
        icon_kind = "exclamation-sign" if s.is_rerouted else "info-sign"
        folium.Marker(
            location=[lat, lng],
            popup=folium.Popup(
                f"<b>{s.step_index}. {s.poi_name}</b><br>"
                f"{s.start_time} · {s.duration_min}min<br>"
                f"{s.rationale[:120]}",
                max_width=280,
            ),
            tooltip=f"{s.step_index}. {s.poi_name}",
            icon=folium.Icon(color=color, icon=icon_kind),
        ).add_to(m)

    if len(polyline_pts) >= 2:
        folium.PolyLine(polyline_pts, color="#3366cc", weight=4, opacity=0.7,
                        dash_array="6, 6").add_to(m)

    st_folium(m, height=480, width=None, returned_objects=[])


def _resolve_coords(poi_ids: list[str]) -> dict[str, tuple[float, float]]:
    if not poi_ids:
        return {}
    conn = get_conn()
    placeholders = ",".join(["?"] * len(poi_ids))
    rows = conn.execute(
        f"SELECT id, longitude, latitude FROM pois WHERE id IN ({placeholders})",
        poi_ids,
    ).fetchall()
    conn.close()
    return {
        r["id"]: (r["longitude"], r["latitude"])
        for r in rows if r["longitude"] is not None
    }
