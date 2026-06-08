"""Fetch real Beijing POIs from AMap Web Service into the BJ-Pal JSONL schema.

The script writes a cache used by scripts/build_mock_data.py. It never stores
the API key; pass it via AMAP_WEB_KEY / AMAP_API_KEY / AMAP_KEY or .env.

Usage:
    AMAP_WEB_KEY=... python3 scripts/fetch_amap_real_pois.py
    python3 scripts/build_mock_data.py
    python3 src/loader.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUT_FILE = DATA / "amap" / "real" / "amap_beijing_real_pois.jsonl"

AREA_CENTERS: dict[str, tuple[float, float]] = {
    "五道营-雍和宫片区": (116.4166, 39.9474),
    "王府井-东单片区": (116.4174, 39.9094),
    "什刹海-鼓楼片区": (116.3877, 39.9456),
    "天安门-故宫片区": (116.3974, 39.9087),
    "三里屯片区": (116.4540, 39.9368),
    "798艺术区片区": (116.494784, 39.985063),
    "奥林匹克公园片区": (116.3974, 40.0028),
    "景山-什刹海片区": (116.3909, 39.9288),
    "东四-本地餐饮片区": (116.4178, 39.9265),
    "前门-大栅栏片区": (116.3990, 39.8959),
    "西单片区": (116.37296, 39.910884),
    "东直门-簋街片区": (116.424768, 39.941746),
    "五道口片区": (116.338611, 39.992552),
    "望京片区": (116.481075, 39.996743),
    "亮马桥片区": (116.46343, 39.949958),
    "国贸-CBD片区": (116.459288, 39.910882),
    "朝阳公园片区": (116.482276, 39.944093),
    "中国美术馆-五四大街片区": (116.408939, 39.925255),
    "牛街片区": (116.363972, 39.884264),
}

AMAP_TYPE_GROUPS = {
    "food": "050000",
    "shopping": "060000",
    "sports": "080000",
    "scenic": "110000",
    "museum": "140000",
}

CORE_KEYWORDS = [
    "雍和宫",
    "五道营胡同",
    "国子监",
    "地坛公园",
    "方砖厂69号炸酱面 雍和宫",
    "胡同咖啡 五道营",
    "王府井步行街",
    "王府井百货",
    "全聚德 王府井",
    "吴裕泰 王府井",
    "故宫博物院",
    "景山公园",
    "前门大街",
    "全聚德 前门",
    "故宫角楼咖啡",
    "什刹海",
    "鼓楼",
    "烟袋斜街",
    "胡大饭馆 簋街",
    "三里屯太古里",
    "三联韬奋书店 三里屯",
    "798艺术区",
    "UCCA尤伦斯当代艺术中心",
    "奥林匹克森林公园",
    "鸟巢",
    "大栅栏",
    "鲜鱼口",
    "西单大悦城",
    "簋街",
    "东直门来福士",
    "五道口",
    "华清嘉园 五道口",
    "望京SOHO",
    "麒麟社 望京",
    "亮马桥",
    "蓝色港湾",
    "国贸商城",
    "SKP 大望路",
    "朝阳公园",
    "中国美术馆",
    "五四大街",
    "牛街",
    "聚宝源 牛街",
]

CATEGORY_LV1_ALLOW = {
    "餐饮服务",
    "风景名胜",
    "科教文化服务",
    "购物服务",
    "体育休闲服务",
}


def main() -> None:
    args = _parse_args()
    key = _load_key()
    if not key:
        raise SystemExit(
            "AMap key missing. Set AMAP_WEB_KEY / AMAP_API_KEY / AMAP_KEY or add one to .env."
        )

    rows: dict[str, dict] = {}

    for keyword in CORE_KEYWORDS:
        for poi in _fetch_text(key, keyword, offset=args.offset):
            row = _normalize_poi(poi, source_area_anchor=_guess_area_for_poi(poi))
            if row:
                rows[row["provider_poi_id"]] = row

    total_calls = len(CORE_KEYWORDS)
    for area, center in AREA_CENTERS.items():
        for type_name, type_code in AMAP_TYPE_GROUPS.items():
            for page in range(1, args.pages + 1):
                pois = _fetch_around(
                    key,
                    center=center,
                    types=type_code,
                    radius=args.radius,
                    page=page,
                    offset=args.offset,
                )
                total_calls += 1
                for poi in pois:
                    row = _normalize_poi(poi, source_area_anchor=area)
                    if row:
                        rows[row["provider_poi_id"]] = row
                time.sleep(args.sleep)

    output_rows = sorted(
        rows.values(),
        key=lambda p: (
            p.get("raw_poi", {}).get("source_area_anchor", ""),
            p.get("category_lv1", ""),
            p.get("name", ""),
        ),
    )
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output_rows),
        encoding="utf-8",
    )

    print(f"[ok] wrote {len(output_rows)} real AMap POIs -> {OUTPUT_FILE}")
    print(f"[ok] amap calls={total_calls}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch real AMap POIs for BJ-Pal")
    parser.add_argument("--pages", type=int, default=4, help="pages per area/type")
    parser.add_argument("--offset", type=int, default=25, help="AMap page size")
    parser.add_argument("--radius", type=int, default=3000, help="around radius in meters")
    parser.add_argument("--sleep", type=float, default=0.05, help="delay between calls")
    return parser.parse_args()


def _load_key() -> str:
    for name in ("AMAP_WEB_KEY", "AMAP_API_KEY", "AMAP_KEY"):
        if os.environ.get(name):
            return os.environ[name].strip()
    env_path = ROOT / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() in {"AMAP_WEB_KEY", "AMAP_API_KEY", "AMAP_KEY"}:
            return value.strip().strip('"').strip("'")
    return ""


def _fetch_text(key: str, keywords: str, *, offset: int) -> list[dict]:
    params = {
        "key": key,
        "keywords": keywords,
        "city": "北京",
        "citylimit": "true",
        "children": "0",
        "offset": str(offset),
        "page": "1",
        "extensions": "all",
    }
    return _request("https://restapi.amap.com/v3/place/text", params)


def _fetch_around(
    key: str,
    *,
    center: tuple[float, float],
    types: str,
    radius: int,
    page: int,
    offset: int,
) -> list[dict]:
    params = {
        "key": key,
        "location": f"{center[0]},{center[1]}",
        "types": types,
        "radius": str(radius),
        "sortrule": "weight",
        "children": "0",
        "offset": str(offset),
        "page": str(page),
        "extensions": "all",
    }
    return _request("https://restapi.amap.com/v3/place/around", params)


def _request(url: str, params: dict[str, str]) -> list[dict]:
    safe_params = dict(params)
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "bj-pal-data-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") != "1":
        safe_params["key"] = "***"
        print(
            f"[warn] AMap request failed info={payload.get('info')} params={safe_params}",
            file=sys.stderr,
        )
        return []
    pois = payload.get("pois") or []
    return [p for p in pois if isinstance(p, dict)]


def _normalize_poi(poi: dict[str, Any], *, source_area_anchor: str) -> dict | None:
    name = _flatten_str(poi.get("name"))
    poi_id = _flatten_str(poi.get("id"))
    location = _flatten_str(poi.get("location"))
    type_text = _flatten_str(poi.get("type"))
    if not name or not poi_id or not location or "," not in location:
        return None
    lng_s, lat_s = location.split(",", 1)
    lng = _to_float(lng_s)
    lat = _to_float(lat_s)
    if lng is None or lat is None:
        return None

    parts = [p for p in type_text.split(";") if p]
    category_lv1 = parts[0] if parts else ""
    if category_lv1 not in CATEGORY_LV1_ALLOW:
        return None

    biz_ext = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
    photos = poi.get("photos") if isinstance(poi.get("photos"), list) else []
    raw = {
        "source": "amap_web_service",
        "source_area_anchor": source_area_anchor,
        "amap": poi,
    }
    return {
        "provider_poi_id": f"AMAP_{poi_id}",
        "name": name,
        "category_lv1": category_lv1,
        "category_lv2": parts[1] if len(parts) > 1 else "",
        "category_lv3": parts[2] if len(parts) > 2 else "",
        "typecode": _flatten_str(poi.get("typecode")),
        "district": _flatten_str(poi.get("adname")),
        "business_area": _business_area(poi, source_area_anchor),
        "address": _flatten_str(poi.get("address")),
        "longitude": round(lng, 6),
        "latitude": round(lat, 6),
        "rating": _to_float(biz_ext.get("rating")),
        "avg_price": _to_float(biz_ext.get("cost")),
        "phone": _flatten_str(poi.get("tel")),
        "photos": photos,
        "raw_poi": raw,
    }


def _business_area(poi: dict[str, Any], source_area_anchor: str) -> str:
    value = _flatten_str(poi.get("business_area"))
    if value:
        return value
    return source_area_anchor.replace("片区", "")


def _guess_area_for_poi(poi: dict[str, Any]) -> str:
    location = _flatten_str(poi.get("location"))
    if "," not in location:
        return "北京"
    lng_s, lat_s = location.split(",", 1)
    lng = _to_float(lng_s)
    lat = _to_float(lat_s)
    if lng is None or lat is None:
        return "北京"
    best_area = ""
    best_dist = float("inf")
    for area, center in AREA_CENTERS.items():
        dist = (lng - center[0]) ** 2 + (lat - center[1]) ** 2
        if dist < best_dist:
            best_area = area
            best_dist = dist
    return best_area or "北京"


def _flatten_str(value: Any) -> str:
    if value is None or value == []:
        return ""
    if isinstance(value, list):
        return ",".join(str(v) for v in value if v)
    return str(value).strip()


def _to_float(value: Any) -> float | None:
    value = _flatten_str(value)
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


if __name__ == "__main__":
    main()
