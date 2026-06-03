"""folium 地图：plan 中各 POI 的 marker + 步行 polyline。"""

from __future__ import annotations

import html
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
    "citywalk": "#2563eb", "meal": "#9f3d34", "culture": "#7c3aed",
    "rest": "#d97706", "shopping": "#15803d", "snack": "#be123c",
    "depart": "#6b7280",
}
MAP_MARKER_RADIUS = 5
MAP_MARKER_WEIGHT = 2
MAP_NUMBER_MARKER_SIZE = 24
MAP_NUMBER_MARKER_CLASS = "bjpal-map-number-marker"
MAP_ROUTE_COLOR = "#0f766e"
MAP_ROUTE_WEIGHT = 5
MAP_ROUTE_OPACITY = 0.92
MAP_ROUTE_HALO_COLOR = "#ffffff"
MAP_ROUTE_HALO_WEIGHT = 9
MAP_ROUTE_DASH_ARRAY = None
MAP_TILE_NAME = "高德地图"
MAP_TILE_URL = (
    "https://webrd02.is.autonavi.com/appmaptile?"
    "lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}"
)
MAP_TILE_ATTRIBUTION = "高德地图"
MAP_SHOW_SCALE_CONTROL = False
MAP_SHOW_ATTRIBUTION_CONTROL = False
MAP_VISUALIZATION_CAPTION = "规划结果可视化图"


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
    m = folium.Map(
        location=[center[1], center[0]],
        zoom_start=15,
        control_scale=MAP_SHOW_SCALE_CONTROL,
        tiles=None,
        attribution_control=MAP_SHOW_ATTRIBUTION_CONTROL,
    )
    folium.TileLayer(
        tiles=MAP_TILE_URL,
        attr=MAP_TILE_ATTRIBUTION,
        name=MAP_TILE_NAME,
        control=False,
    ).add_to(m)

    visible_stops = []
    for s in plan.steps:
        if not s.poi_id or s.kind == "depart":
            continue
        coords = poi_coords.get(s.poi_id)
        if not coords:
            continue
        lng, lat = coords
        color = "#9f3d34" if s.is_rerouted else KIND_COLOR.get(s.kind, "#2563eb")
        visible_stops.append((s, lat, lng, color))

    polyline_pts = [(lat, lng) for _, lat, lng, _ in visible_stops]

    if len(polyline_pts) >= 2:
        folium.PolyLine(
            polyline_pts,
            color=MAP_ROUTE_HALO_COLOR,
            weight=MAP_ROUTE_HALO_WEIGHT,
            opacity=0.9,
        ).add_to(m)
        folium.PolyLine(
            polyline_pts,
            color=MAP_ROUTE_COLOR,
            weight=MAP_ROUTE_WEIGHT,
            opacity=MAP_ROUTE_OPACITY,
            dash_array=MAP_ROUTE_DASH_ARRAY,
        ).add_to(m)

    for s, lat, lng, color in visible_stops:
        marker_html = _numbered_marker_html(
            s.step_index,
            color=color,
            is_rerouted=s.is_rerouted,
        )
        folium.Marker(
            location=[lat, lng],
            icon=folium.DivIcon(
                icon_size=(MAP_NUMBER_MARKER_SIZE, MAP_NUMBER_MARKER_SIZE),
                icon_anchor=(MAP_NUMBER_MARKER_SIZE // 2, MAP_NUMBER_MARKER_SIZE // 2),
                html=marker_html,
            ),
            popup=folium.Popup(
                f"<b>{s.step_index}. {html.escape(s.poi_name or '')}</b><br>"
                f"{html.escape(s.start_time or '')} · {s.duration_min}min<br>"
                f"{html.escape((s.rationale or '')[:120])}",
                max_width=280,
            ),
            tooltip=f"{s.step_index}. {s.poi_name}",
        ).add_to(m)

    st_folium(m, height=480, width=None, returned_objects=[])
    st.caption(MAP_VISUALIZATION_CAPTION)


def _numbered_marker_html(step_index: int, *, color: str, is_rerouted: bool = False) -> str:
    """Return a compact numbered marker so map pins match timeline order."""
    size = MAP_NUMBER_MARKER_SIZE + (2 if is_rerouted else 0)
    border = MAP_MARKER_WEIGHT + (1 if is_rerouted else 0)
    return (
        f"<div class='{MAP_NUMBER_MARKER_CLASS}' style='"
        f"width:{size}px;height:{size}px;border-radius:999px;"
        f"background:{color};border:{border}px solid #ffffff;"
        f"box-shadow:0 2px 8px rgba(15,23,42,.24);"
        f"color:#ffffff;font-size:12px;font-weight:800;"
        f"line-height:{size}px;text-align:center;"
        f"font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;'>"
        f"{int(step_index)}</div>"
    )


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
