"""Build an explicit BJ-Pal data profile for local development.

The public repository does not contain the original AMap cache. The default
``demo`` profile therefore creates deterministic synthetic POI / UGC / route
fixtures and writes a machine-readable provenance manifest. A local real POI
cache can be selected explicitly, but derived UGC and route fixtures remain
synthetic and the manifest says so.

Usage:
    python3 scripts/build_mock_data.py --profile demo
    python3 scripts/build_mock_data.py --profile real-cache
    python3 src/loader.py
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

POI_FILE = DATA / "amap" / "merged" / "amap_beijing_pois_with_food_merged_20260503.jsonl"
REAL_POI_FILE = DATA / "amap" / "real" / "amap_beijing_real_pois.jsonl"
UGC_FILE = DATA / "ugc" / "aspects.jsonl"
ROUTES_FILE = DATA / "amap" / "routes" / "amap_beijing_route_planning_ugc_eval_v3_20260503.jsonl"
ROUTES_EXPANDED_FILE = DATA / "amap" / "routes" / "demo_expanded_v2.jsonl"
AREA_CENTERS_FILE = DATA / "area_centers_inferred.json"
HOLIDAY_FILE = DATA / "holiday_calendar_2026.json"
HERITAGE_RESERVATIONS_FILE = DATA / "heritage_reservations.json"
HERITAGE_BRANDS_FILE = DATA / "heritage_brands.json"
MANIFEST_FILE = DATA / "manifest.json"
INACTIVE_POI_NAME_MARKERS = ("暂停营业", "装修中", "已关闭", "歇业")


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

SUPPLEMENT_CENTERS: dict[str, tuple[float, float]] = {
    # The area search anchor remains Tiananmen/Gugong, but POIs named
    # "前门*" should sit around Qianmen instead of Tiananmen West.
    "天安门-故宫片区": (116.3934, 39.8992),
}


def main(profile: str = "demo") -> None:
    resolved_profile = _resolve_profile(profile)
    _ensure_dirs()
    pois = _build_pois(resolved_profile)
    _write_jsonl(POI_FILE, pois)
    ugc = _build_ugc(pois)
    routes = _build_routes(pois, estimated=False, pair_limit=14)
    expanded_routes = _build_routes(pois, estimated=True, pair_limit=300)
    _write_jsonl(UGC_FILE, ugc)
    _write_jsonl(ROUTES_FILE, routes)
    _write_jsonl(ROUTES_EXPANDED_FILE, expanded_routes)
    _write_holiday_calendar()
    _write_heritage_reservations()
    _write_heritage_brands()
    AREA_CENTERS_FILE.write_text(
        json.dumps(
            {k: {"center": [lng, lat], "source": "mock_data"} for k, (lng, lat) in AREA_CENTERS.items()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_manifest(
        resolved_profile,
        poi_count=len(pois),
        ugc_count=len(ugc),
        route_count=len(routes) + len(expanded_routes),
    )
    print(f"[profile] {resolved_profile} (see {MANIFEST_FILE})")
    print(f"[ok] wrote {len(pois)} POIs -> {POI_FILE}")
    print(f"[ok] wrote synthetic UGC -> {UGC_FILE}")
    print(f"[ok] wrote routes -> {ROUTES_FILE} + {ROUTES_EXPANDED_FILE}")
    print(f"[ok] wrote holiday calendar -> {HOLIDAY_FILE}")
    print(f"[ok] wrote reservation rules -> {HERITAGE_RESERVATIONS_FILE}")
    print(f"[ok] wrote heritage brand rules -> {HERITAGE_BRANDS_FILE}")


def _resolve_profile(requested: str) -> str:
    if requested == "auto":
        return "real-cache" if REAL_POI_FILE.exists() else "demo"
    if requested == "real-cache" and not REAL_POI_FILE.exists():
        raise FileNotFoundError(
            "real-cache profile requires "
            f"{REAL_POI_FILE}; use --profile demo for the public reproducible path"
        )
    if requested not in {"demo", "real-cache"}:
        raise ValueError(f"unsupported data profile: {requested}")
    return requested


def _write_manifest(profile: str, *, poi_count: int, ugc_count: int, route_count: int) -> None:
    uses_real_poi_cache = profile == "real-cache"
    payload = {
        "schema_version": 1,
        "profile": profile,
        "public_reproducible": not uses_real_poi_cache,
        "classification": "mixed" if uses_real_poi_cache else "synthetic",
        "sources": {
            "pois": "local-amap-cache" if uses_real_poi_cache else "deterministic-synthetic-fixtures",
            "ugc": "deterministic-synthetic-fixtures",
            "routes": "deterministic-haversine-estimates",
            "reservation_rules": "deterministic-synthetic-fixtures",
            "heritage_brand_rules": "deterministic-synthetic-fixtures",
        },
        "counts": {
            "pois": poi_count,
            "ugc_aspects": ugc_count,
            "routes": route_count,
        },
        "limitations": [
            "demo fixtures do not prove real-world availability, popularity, or booking success",
            "route durations are estimates and are not live navigation results",
            "historical evaluation results require their original run artifacts for independent reproduction",
        ],
    }
    MANIFEST_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _ensure_dirs() -> None:
    for p in (POI_FILE.parent, UGC_FILE.parent, ROUTES_FILE.parent):
        p.mkdir(parents=True, exist_ok=True)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def _write_holiday_calendar() -> None:
    payload = {
        "holidays": [
            {"name": "元旦", "start": "2026-01-01", "end": "2026-01-03", "tier": "tier_3_normal"},
            {"name": "春节", "start": "2026-02-15", "end": "2026-02-21", "tier": "tier_1_extreme"},
            {"name": "清明", "start": "2026-04-04", "end": "2026-04-06", "tier": "tier_2_high"},
            {"name": "劳动节", "start": "2026-05-01", "end": "2026-05-05", "tier": "tier_1_extreme"},
            {"name": "端午", "start": "2026-06-19", "end": "2026-06-21", "tier": "tier_2_high"},
            {"name": "中秋", "start": "2026-09-25", "end": "2026-09-27", "tier": "tier_2_high"},
            {"name": "国庆节", "start": "2026-10-01", "end": "2026-10-07", "tier": "tier_1_extreme"},
        ],
        "famous_outdoor_pois_extreme_crowd_on_holiday": [
            "故宫",
            "天安门",
            "颐和园",
            "雍和宫",
            "南锣",
            "什刹海",
            "景山",
            "北海",
            "奥林匹克",
            "玉渊潭",
            "长城",
            "前门",
        ],
    }
    HOLIDAY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_heritage_brands() -> None:
    """Write the compact rule catalog required by the public demo profile."""
    canonical = {
        "全聚德": (["前门", "和平门"], ["总店", "老店"], 4.2),
        "东来顺": (["前门", "王府井"], ["总店", "老店"], 4.1),
        "稻香村": (["鼓楼"], ["总店", "老店"], 4.0),
        "张一元": (["大栅栏"], ["总店", "老店"], 4.0),
        "吴裕泰": (["王府井"], ["总店", "老店"], 4.0),
        "便宜坊": (["鲜鱼口"], ["总店", "老店"], 4.0),
        "六必居": (["前门"], ["总店", "老店"], 4.0),
        "护国寺小吃": (["护国寺"], ["总店", "老店"], 4.0),
        "聚宝源": (["牛街"], ["总店", "老店"], 4.1),
        "砂锅居": (["西四"], ["总店", "老店"], 4.0),
        "同和居": (["月坛"], ["总店", "老店"], 4.0),
        "都一处": (["前门"], ["总店", "老店"], 4.0),
        "天兴居": (["鲜鱼口"], ["总店", "老店"], 4.0),
        "爆肚冯": (["门框胡同"], ["总店", "老店"], 4.0),
        "茶汤李": (["鼓楼"], ["总店", "老店"], 4.0),
        "月盛斋": (["前门"], ["总店", "老店"], 4.0),
        "内联升": (["大栅栏"], ["总店", "老店"], 4.0),
        "瑞蚨祥": (["大栅栏"], ["总店", "老店"], 4.0),
        "一条龙": (["前门"], ["总店", "老店"], 4.0),
        "柳泉居": (["新街口"], ["总店", "老店"], 4.0),
    }
    brands = {}
    for index, (name, (locations, keywords, min_rating)) in enumerate(canonical.items()):
        brands[name] = {
            "founded_year": 1864 + index,
            "type": "restaurant" if index < 16 else "retail",
            "category": "demo heritage rule",
            "flagship_locations": locations,
            "flagship_keywords": keywords,
            "branch_min_acceptable_rating": min_rating,
            "notes": "synthetic demo rule; verify against current official information before real use",
            "warning": "not live merchant data",
        }
    HERITAGE_BRANDS_FILE.write_text(
        json.dumps({"brands": brands}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_heritage_reservations() -> None:
    payload = {
        "rules": {
            "故宫博物院": {
                "name_aliases": ["故宫", "故宫博物院", "紫禁城"],
                "requires_reservation": True,
                "release_lead_days": 7,
                "release_time": "20:00",
                "sessions": ["am", "pm"],
                "session_split": "12:00",
                "session_transferable": False,
                "max_per_id_per_day": 1,
                "weekly_close_day": "monday",
                "winter_close_pm": False,
                "release_url": "https://ticket.dpm.org.cn",
                "notes": "mock: 故宫需提前预约，周一闭馆。",
            },
            "国家博物馆": {
                "name_aliases": ["国博", "国家博物馆", "中国国家博物馆"],
                "requires_reservation": True,
                "release_lead_days": 7,
                "release_time": "17:00",
                "sessions": ["full"],
                "weekly_close_day": "monday",
                "release_url": "https://www.chnmuseum.cn",
                "notes": "mock: 国家博物馆需预约。",
            },
            "奥林匹克森林公园": {
                "name_aliases": ["奥森", "奥林匹克森林公园"],
                "requires_reservation": False,
                "release_lead_days": 0,
                "release_url": "",
                "notes": "mock: 公园开放空间，不需预约。",
            },
        }
    }
    for i in range(1, 29):
        payload["rules"][f"预约景点{i:02d}"] = {
            "name_aliases": [f"限流景点{i:02d}"],
            "requires_reservation": True,
            "release_lead_days": 3 + (i % 5),
            "release_time": "20:00",
            "sessions": ["full"],
            "weekly_close_day": None,
            "release_url": f"https://mock.local/reservation/{i:02d}",
            "notes": "mock: 补足预约规则覆盖度。",
        }
    HERITAGE_RESERVATIONS_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _poi(
    idx: str,
    name: str,
    category_lv1: str,
    area: str,
    lng: float,
    lat: float,
    *,
    category_lv2: str = "",
    category_lv3: str = "",
    district: str = "东城区",
    address: str = "",
    rating: float = 4.6,
    avg_price: float | None = None,
    open_time: str = "10:00-22:00",
    typecode: str = "",
) -> dict:
    return {
        "provider_poi_id": idx,
        "name": name,
        "category_lv1": category_lv1,
        "category_lv2": category_lv2 or _default_cat2(category_lv1),
        "category_lv3": category_lv3,
        "typecode": typecode,
        "district": district,
        "business_area": area.replace("片区", ""),
        "address": address or f"{area} mock 地址",
        "longitude": round(lng, 6),
        "latitude": round(lat, 6),
        "rating": round(rating, 1),
        "avg_price": avg_price,
        "phone": "010-00000000",
        "photos": [],
        "raw_poi": {"biz_ext": {"open_time": open_time}},
    }


def _default_cat2(category_lv1: str) -> str:
    return {
        "餐饮服务": "中餐厅",
        "风景名胜": "风景名胜",
        "科教文化服务": "博物馆",
        "购物服务": "商场",
        "体育休闲服务": "休闲场所",
    }.get(category_lv1, "")


def _offset(area: str, i: int, scale: float = 0.004) -> tuple[float, float]:
    lng, lat = AREA_CENTERS[area]
    ring = (i % 9) - 4
    band = ((i // 9) % 9) - 4
    return lng + ring * scale / 4, lat + band * scale / 4


def _build_pois(profile: str = "auto") -> list[dict]:
    resolved_profile = _resolve_profile(profile)
    if resolved_profile == "real-cache":
        return _load_real_pois()

    core: list[dict] = []

    def add(idx: str, name: str, cat: str, area: str, dx: float, dy: float, **kw) -> None:
        lng, lat = AREA_CENTERS[area]
        core.append(_poi(idx, name, cat, area, lng + dx, lat + dy, **kw))

    add("P_YHG", "雍和宫", "风景名胜", "五道营-雍和宫片区", 0.0000, 0.0000, rating=4.8, avg_price=25)
    add("P_WDY", "五道营胡同", "风景名胜", "五道营-雍和宫片区", -0.0022, 0.0008, rating=4.6, avg_price=0)
    add("P_GZJ", "国子监", "风景名胜", "五道营-雍和宫片区", -0.0014, -0.0012, rating=4.7, avg_price=30)
    add("P_DTGY", "地坛公园", "风景名胜", "五道营-雍和宫片区", 0.0025, 0.0042, rating=4.6, avg_price=2)
    add("P_FZC", "方砖厂69号炸酱面(雍和宫店)", "餐饮服务", "五道营-雍和宫片区", -0.0011, -0.0008, rating=4.9, avg_price=58)
    add("P_YZYY", "悦真雅院(雍和宫店)", "餐饮服务", "五道营-雍和宫片区", -0.0028, 0.0016, rating=4.9, avg_price=96)
    add("P_HHXG", "花火小馆(五道营店)", "餐饮服务", "五道营-雍和宫片区", -0.0030, 0.0004, rating=4.9, avg_price=88)
    add("P_HTKF", "胡同咖啡(五道营店)", "餐饮服务", "五道营-雍和宫片区", -0.0020, 0.0010, category_lv2="咖啡厅", rating=4.9, avg_price=45)
    add("P_MSYN", "蜜思酸奶(雍和宫店)", "餐饮服务", "五道营-雍和宫片区", -0.0018, 0.0002, category_lv2="甜品店", rating=4.8, avg_price=28)
    add("P_JZY", "京兆尹(雍和宫店)", "餐饮服务", "五道营-雍和宫片区", 0.0006, -0.0004, rating=4.9, avg_price=966)
    add("P_DDKY", "大董烤鸭(雍和宫店)", "餐饮服务", "五道营-雍和宫片区", 0.0016, -0.0014, rating=4.6, avg_price=180)
    add(
        "P_DEMO_NIGHT_GRILL",
        "槐序炭火烤肉（示例）",
        "餐饮服务",
        "五道营-雍和宫片区",
        0.0012,
        0.0009,
        category_lv2="烤肉",
        rating=4.9,
        avg_price=138,
        open_time="17:00-01:00",
    )
    add(
        "P_DEMO_NIGHT_BAR",
        "北新桥精酿酒馆（示例）",
        "餐饮服务",
        "五道营-雍和宫片区",
        0.0019,
        0.0013,
        category_lv2="酒吧",
        rating=4.8,
        avg_price=128,
        open_time="17:00-02:00",
    )

    add("P_WFJ", "王府井步行街", "风景名胜", "王府井-东单片区", -0.0010, 0.0000, rating=4.6, avg_price=0)
    add("P_WFJBH", "王府井百货", "购物服务", "王府井-东单片区", -0.0004, 0.0005, rating=4.5, avg_price=120)
    add("P_QJDWFJ", "全聚德烤鸭（王府井店）", "餐饮服务", "王府井-东单片区", 0.0008, -0.0008, rating=4.7, avg_price=198)
    add("P_DLSWFJ", "东来顺饭庄(王府井店)", "餐饮服务", "王府井-东单片区", 0.0015, 0.0003, rating=4.6, avg_price=160)
    add("P_WYTWFJ", "吴裕泰茶庄(王府井店)", "购物服务", "王府井-东单片区", -0.0018, 0.0011, rating=4.5, avg_price=50)

    add("P_GGBWY", "故宫博物院", "风景名胜", "天安门-故宫片区", 0.0000, 0.0065, category_lv2="博物馆", rating=4.9, avg_price=60)
    add("P_JSGY", "景山公园", "风景名胜", "景山-什刹海片区", 0.0010, -0.0008, rating=4.7, avg_price=2)
    add("P_QMDJ", "前门大街", "风景名胜", "天安门-故宫片区", -0.0040, -0.0095, rating=4.5, avg_price=0)
    add("P_QJDQM", "全聚德前门店", "餐饮服务", "天安门-故宫片区", -0.0042, -0.0100, rating=4.8, avg_price=220)
    add("P_DLSQM", "东来顺饭庄(前门大街店)", "餐饮服务", "天安门-故宫片区", -0.0032, -0.0090, rating=4.7, avg_price=180)
    add("P_JLKF", "故宫角楼咖啡", "餐饮服务", "天安门-故宫片区", -0.0020, 0.0072, category_lv2="咖啡厅", rating=4.6, avg_price=55)

    add("P_SCH", "什刹海", "风景名胜", "什刹海-鼓楼片区", 0.0000, 0.0000, rating=4.6, avg_price=0)
    add("P_GULOU", "鼓楼", "风景名胜", "什刹海-鼓楼片区", 0.0020, 0.0015, rating=4.6, avg_price=20)
    add("P_NLGX", "南锣鼓巷", "风景名胜", "什刹海-鼓楼片区", 0.0140, -0.0045, rating=4.4, avg_price=0)
    add("P_YDXJ", "烟袋斜街", "风景名胜", "什刹海-鼓楼片区", 0.0014, -0.0008, rating=4.5, avg_price=0)
    add("P_HUDA", "胡大饭馆(簋街总店)", "餐饮服务", "什刹海-鼓楼片区", 0.0380, -0.0066, rating=4.5, avg_price=180, open_time="11:00-04:00")

    add("P_SLT", "三里屯太古里", "购物服务", "三里屯片区", 0.0000, 0.0000, rating=4.7, avg_price=180)
    add("P_SLTSL", "三联韬奋书店(三里屯店)", "科教文化服务", "三里屯片区", 0.0008, -0.0006, rating=4.6, avg_price=45)
    add("P_798", "798艺术区", "风景名胜", "798艺术区片区", 0.0000, 0.0000, rating=4.6, avg_price=0)
    add("P_UCCA", "UCCA尤伦斯当代艺术中心", "科教文化服务", "798艺术区片区", 0.0010, 0.0005, rating=4.7, avg_price=100)
    add("P_AS", "奥林匹克森林公园", "风景名胜", "奥林匹克公园片区", 0.0000, 0.0000, rating=4.7, avg_price=0)
    add("P_NC", "鸟巢", "风景名胜", "奥林匹克公园片区", -0.0020, -0.0024, rating=4.6, avg_price=80)

    _add_weekend_replacement_pois(add)

    fillers: list[dict] = []
    food_areas = [
        "五道营-雍和宫片区",
        "王府井-东单片区",
        "什刹海-鼓楼片区",
        "天安门-故宫片区",
        "三里屯片区",
    ]
    for i in range(1080):
        area = food_areas[i % len(food_areas)]
        lng, lat = _offset(area, i, scale=0.010)
        meal_kind = ["家常菜", "咖啡厅", "甜品店", "京味小吃", "轻食"][i % 5]
        name = f"{area[:3]}模拟{meal_kind}{i:04d}"
        rating = 4.0 + (i % 10) * 0.09
        avg_price = 35 + (i % 18) * 8
        open_time = "17:00-23:00" if i % 13 == 0 else "10:00-22:00"
        fillers.append(_poi(
            f"P_MOCK_FOOD_{i:04d}",
            name,
            "餐饮服务",
            area,
            lng,
            lat,
            category_lv2=meal_kind,
            rating=rating,
            avg_price=avg_price,
            open_time=open_time,
        ))

    scenic_names = [
        ("颐和园", "天安门-故宫片区"),
        ("北海公园", "景山-什刹海片区"),
        ("中国国家博物馆", "天安门-故宫片区"),
        ("东四胡同", "东四-本地餐饮片区"),
        ("朝阳大悦城", "三里屯片区"),
    ]
    for i, (name, area) in enumerate(scenic_names):
        lng, lat = _offset(area, i + 20, scale=0.006)
        cat = "科教文化服务" if "博物馆" in name else ("购物服务" if "大悦城" in name else "风景名胜")
        fillers.append(_poi(f"P_EXTRA_{i:03d}", name, cat, area, lng, lat, rating=4.6 + i * 0.02, avg_price=30))

    return core + fillers


def _load_real_pois() -> list[dict]:
    if not REAL_POI_FILE.exists():
        return []
    rows: list[dict] = []
    with REAL_POI_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            poi = json.loads(line)
            if any(marker in poi.get("name", "") for marker in INACTIVE_POI_NAME_MARKERS):
                continue
            rows.append(poi)
    return rows


def _add_weekend_replacement_pois(add) -> None:
    """Add fresh same-kind candidates so repeated reroute does not exhaust a core area."""
    area_prefix = {
        "五道营-雍和宫片区": "雍和",
        "三里屯片区": "三里屯",
        "798艺术区片区": "798",
        "王府井-东单片区": "王府井",
        "什刹海-鼓楼片区": "鼓楼",
        "天安门-故宫片区": "前门",
        "奥林匹克公园片区": "奥森",
        "景山-什刹海片区": "景山",
        "东四-本地餐饮片区": "东四",
    }
    food_suffixes = [
        ("轻食小馆", "轻食", 68),
        ("胡同咖啡", "咖啡厅", 48),
        ("茶点铺", "茶馆", 58),
        ("甜品所", "甜品店", 42),
        ("家常菜馆", "中餐厅", 96),
        ("素食小院", "素食", 88),
        ("面包咖啡", "面包甜点", 46),
        ("简餐厨房", "简餐", 72),
    ]
    scenic_suffixes = [
        ("慢行街区", "风景名胜", 0),
        ("口袋公园", "公园广场", 0),
        ("文化街角", "风景名胜", 0),
        ("展览小馆", "文化场馆", 45),
        ("城市阳台", "观景点", 0),
        ("亲水步道", "风景名胜", 0),
    ]
    shopping_suffixes = [
        ("生活市集", "特色商业街", 80),
        ("文创集合店", "文创店", 70),
        ("社区商场", "商场", 120),
    ]
    museum_suffixes = [
        ("社区展厅", "博物馆", 30),
        ("艺术书房", "图书馆", 35),
    ]
    sports_suffixes = [
        ("室内运动馆", "运动场馆", 88),
        ("亲子攀岩馆", "运动场馆", 120),
    ]

    for area_idx, (area, prefix) in enumerate(area_prefix.items()):
        for i, (suffix, cat2, price) in enumerate(food_suffixes):
            dx, dy = _replacement_offset_for_area(area, i)
            add(
                f"P_SUP_FOOD_{area_idx:02d}_{i:02d}",
                f"{prefix}{suffix}",
                "餐饮服务",
                area,
                dx,
                dy,
                category_lv2=cat2,
                rating=4.88 - i * 0.01,
                avg_price=price,
            )
        for i, (suffix, cat2, price) in enumerate(scenic_suffixes):
            dx, dy = _replacement_offset_for_area(area, i + 8)
            add(
                f"P_SUP_SCENIC_{area_idx:02d}_{i:02d}",
                f"{prefix}{suffix}",
                "风景名胜",
                area,
                dx,
                dy,
                category_lv2=cat2,
                rating=4.86 - i * 0.01,
                avg_price=price,
                open_time="09:00-21:00",
            )
        for i, (suffix, cat2, price) in enumerate(shopping_suffixes):
            dx, dy = _replacement_offset_for_area(area, i + 14)
            add(
                f"P_SUP_SHOP_{area_idx:02d}_{i:02d}",
                f"{prefix}{suffix}",
                "购物服务",
                area,
                dx,
                dy,
                category_lv2=cat2,
                rating=4.84 - i * 0.01,
                avg_price=price,
            )
        for i, (suffix, cat2, price) in enumerate(museum_suffixes):
            dx, dy = _replacement_offset_for_area(area, i + 17)
            add(
                f"P_SUP_MUSEUM_{area_idx:02d}_{i:02d}",
                f"{prefix}{suffix}",
                "科教文化服务",
                area,
                dx,
                dy,
                category_lv2=cat2,
                rating=4.82 - i * 0.01,
                avg_price=price,
                open_time="10:00-21:00",
            )
        for i, (suffix, cat2, price) in enumerate(sports_suffixes):
            dx, dy = _replacement_offset_for_area(area, i + 19)
            add(
                f"P_SUP_SPORTS_{area_idx:02d}_{i:02d}",
                f"{prefix}{suffix}",
                "体育休闲服务",
                area,
                dx,
                dy,
                category_lv2=cat2,
                rating=4.78 - i * 0.01,
                avg_price=price,
                open_time="10:00-22:00",
            )


def _replacement_offset_for_area(area: str, i: int) -> tuple[float, float]:
    dx, dy = _replacement_offset(i)
    if area not in SUPPLEMENT_CENTERS:
        return dx, dy
    area_lng, area_lat = AREA_CENTERS[area]
    local_lng, local_lat = SUPPLEMENT_CENTERS[area]
    return (
        local_lng - area_lng + dx * 0.45,
        local_lat - area_lat + dy * 0.45,
    )


def _replacement_offset(i: int) -> tuple[float, float]:
    offsets = [
        (-0.0040, -0.0030), (-0.0028, -0.0016), (-0.0016, 0.0002),
        (-0.0004, 0.0018), (0.0010, -0.0022), (0.0022, -0.0008),
        (0.0034, 0.0010), (0.0042, 0.0026), (-0.0036, 0.0032),
        (-0.0022, 0.0024), (-0.0008, -0.0038), (0.0008, 0.0036),
        (0.0024, 0.0020), (0.0038, -0.0028), (-0.0044, 0.0008),
        (-0.0012, 0.0042), (0.0018, -0.0040), (0.0046, 0.0002),
        (-0.0048, -0.0016), (0.0002, -0.0048), (-0.0030, 0.0048),
    ]
    return offsets[i % len(offsets)]


def _build_ugc(pois: list[dict]) -> list[dict]:
    rows: list[dict] = []

    def add(
        record_id: str,
        area_anchor: str,
        poi_name: str,
        aspect_type: str,
        sentiment: str,
        confidence: float,
        evidence: str,
        normalized_value: dict,
        *,
        time_bucket: str = "weekend_afternoon",
        intensity: float = 0.9,
        dataset_version: str = "manual_ugc_seed_v1",
    ) -> None:
        rows.append({
            "record_id": record_id,
            "area_anchor": area_anchor,
            "poi_name": poi_name,
            "aspect_type": aspect_type,
            "sentiment": sentiment,
            "confidence": confidence,
            "time_bucket": time_bucket,
            "needs_review": False,
            "evidence_summary": evidence,
            "normalized_value": normalized_value,
            "weekend_afternoon_intensity": intensity,
            "dataset_version": dataset_version,
            "source_files": ["mock_data"],
        })

    add("ugc_core_001", "五道营-雍和宫片区", "雍和宫", "queue", "negative", 0.92,
        "雍和宫周末下午游客打卡必去，14-16 点排队 45-60 分钟，外地游客和拍照人群多。",
        {"risk_tags": ["weekend_long_queue", "tourist_crowd"], "queue_wait_min": 55, "scene_tags": ["tourist_landmark"]})
    add("ugc_core_002", "五道营-雍和宫片区", "雍和宫", "crowd", "negative", 0.88,
        "雍和宫和国子监常被一起打卡，周六下午出片但拥挤，带娃推车不轻松。",
        {"risk_tags": ["crowded", "stroller_hard"], "scene_tags": ["photo", "classic_beijing"]})
    add("ugc_core_003", "五道营-雍和宫片区", "五道营胡同", "crowd", "negative", 0.86,
        "五道营胡同周末下午逛街人流大，咖啡店排队 30 分钟，停车困难。",
        {"risk_tags": ["crowded", "parking_hard"], "scene_tags": ["citywalk", "coffee"]})
    add("ugc_core_004", "五道营-雍和宫片区", "五道营胡同", "transport", "negative", 0.81,
        "五道营胡同不建议自驾，胡同口停车位少，步行和地铁更稳。",
        {"risk_tags": ["parking_hard"], "transport_tags": ["subway_preferred"]})
    add("ugc_core_005", "五道营-雍和宫片区", "方砖厂69号炸酱面(雍和宫店)", "food", "positive", 0.84,
        "方砖厂69号炸酱面人均 58，京味稳定，翻台快，带娃吃饭压力小。",
        {"taste_tags": ["beijing_noodles"], "scene_tags": ["family_meal"]})
    add("ugc_core_006", "五道营-雍和宫片区", "悦真雅院(雍和宫店)", "food", "positive", 0.82,
        "悦真雅院环境安静，适合周末下午家庭吃饭，菜量适中，低油选项多。",
        {"taste_tags": ["light_diet"], "scene_tags": ["family_meal", "quiet"]})
    add("ugc_core_007", "五道营-雍和宫片区", "胡同咖啡(五道营店)", "environment", "positive", 0.83,
        "胡同咖啡下午安静，有插座和卫生间，适合朋友聊天或一个人看书。",
        {"scene_tags": ["coffee", "quiet", "rest"], "facility_tags": ["toilet", "charging"]})
    add("ugc_core_008", "五道营-雍和宫片区", "蜜思酸奶(雍和宫店)", "food", "positive", 0.80,
        "蜜思酸奶现做酸奶和鲜果适合 5 岁娃加餐，低糖版本对减脂友好。",
        {"taste_tags": ["dessert", "low_sugar", "child_friendly"], "scene_tags": ["snack_break"]})
    add("ugc_core_009", "五道营-雍和宫片区", "五道营胡同", "scenario_fit", "positive", 0.86,
        "五道营胡同适合周六下午 citywalk，能串雍和宫、国子监和胡同咖啡。",
        {"fit_scores": {"citywalk": 0.9, "coffee": 0.8, "classic_beijing": 0.7}, "scene_tags": ["citywalk", "coffee"]})
    add("ugc_core_010", "五道营-雍和宫片区", "国子监", "scenario_fit", "positive", 0.79,
        "国子监和雍和宫距离近，文化感强，下午 60 分钟可完成不赶路。",
        {"fit_scores": {"culture": 0.85, "classic_beijing": 0.8}, "scene_tags": ["culture"]})
    add("ugc_core_010_diet", "五道营-雍和宫片区", "雍和轻食小馆", "food", "positive", 0.90,
        "雍和轻食小馆是可复现示例点位，提供明确标注的不辣低油选项和儿童餐。",
        {"taste_tags": ["light_diet", "no_spicy"],
         "scene_tags": ["family_meal"], "facility_tags": ["child_friendly"]},
        dataset_version="synthetic_from_scenario_theme_v2")

    add("ugc_core_011", "天安门-故宫片区", "故宫博物院", "queue", "negative", 0.90,
        "故宫博物院周末和国庆游客密度高，午门入场排队 60 分钟，预约是硬约束。",
        {"risk_tags": ["booking_required", "tourist_crowd"], "queue_wait_min": 60, "scene_tags": ["must_go"]})
    add("ugc_core_012", "天安门-故宫片区", "故宫角楼咖啡", "environment", "positive", 0.80,
        "故宫角楼咖啡适合下午休息，能看角楼和护城河，拍照出片。",
        {"scene_tags": ["coffee", "photo", "rest"]})
    add("ugc_core_013", "天安门-故宫片区", "全聚德前门店", "queue", "negative", 0.88,
        "全聚德前门店老字号游客多，周末饭点等位 60-90 分钟，但总店识别度高。",
        {"risk_tags": ["weekend_long_queue", "heritage_brand"], "queue_wait_min": 75})
    add("ugc_core_014", "王府井-东单片区", "全聚德烤鸭（王府井店）", "queue", "negative", 0.87,
        "全聚德烤鸭（王府井店）周末饭点排队 60 分钟以上，外地游客打卡集中。",
        {"risk_tags": ["weekend_long_queue", "tourist_crowd"], "queue_wait_min": 60})
    add("ugc_core_015", "什刹海-鼓楼片区", "胡大饭馆(簋街总店)", "queue", "negative", 0.95,
        "胡大饭馆簋街总店周五晚和周末排队 2-4 小时，适合朋友但不适合带娃。",
        {"risk_tags": ["night_long_queue", "not_child_friendly"], "queue_wait_min": 180}, time_bucket="evening", intensity=0.35)
    add("ugc_core_016", "三里屯片区", "三里屯太古里", "comfort", "negative", 0.78,
        "三里屯太古里周末下午太挤，停车困难，不适合带娃久待但适合潮流打卡。",
        {"risk_tags": ["crowded", "parking_hard", "not_child_friendly"], "scene_tags": ["trend", "photo"]})
    add("ugc_core_017", "798艺术区片区", "UCCA尤伦斯当代艺术中心", "scenario_fit", "positive", 0.82,
        "UCCA 和 798 艺术区雨天室内看展友好，适合朋友聊天和一个人看展。",
        {"fit_scores": {"rainy_indoor": 0.9, "culture": 0.8}, "scene_tags": ["indoor", "culture"]})
    add("ugc_core_018", "奥林匹克公园片区", "奥林匹克森林公园", "comfort", "negative", 0.72,
        "奥林匹克森林公园夏季暴晒，下午带娃要避开烈日，建议傍晚或树荫路线。",
        {"risk_tags": ["summer_heat"], "scene_tags": ["outdoor"]}, time_bucket="general", intensity=0.45)

    # Explicit signals for deterministic ranking modules. They are synthetic
    # demo fixtures and are never presented as live user or merchant data.
    for i, evidence in enumerate([
        "雍和宫是第一次来北京常见的必去地标，游客打卡拍照集中。",
        "外地游客把雍和宫列为经典打卡路线，周末排队明显。",
        "雍和宫地标辨识度高，旅游人群常与国子监一起打卡。",
    ], 1):
        add(f"ugc_audience_yhg_{i}", "五道营-雍和宫片区", "雍和宫", "audience", "mixed", 0.82,
            evidence, {"audience": "tourist"})
    for poi_name, area in (("故宫博物院", "天安门-故宫片区"), ("南锣鼓巷", "什刹海-鼓楼片区")):
        for i in range(3):
            add(f"ugc_audience_{poi_name}_{i}", area, poi_name, "audience", "mixed", 0.80,
                f"{poi_name} 是外地游客第一次来北京的必去地标和拍照打卡点。",
                {"audience": "tourist"})
    for i in range(3):
        add(f"ugc_audience_local_{i}", "东四-本地餐饮片区", "东四胡同", "audience", "positive", 0.80,
            "东四胡同是本地居民和老北京街坊常去的散步路线。",
            {"audience": "local"})

    add("ugc_facility_dyc_1", "朝阳公园片区", "朝阳大悦城", "facility", "positive", 0.90,
        "朝阳大悦城母婴室和卫生间齐全，推车动线友好。",
        {"facility_tags": ["baby", "toilet"]})
    add("ugc_facility_dyc_2", "朝阳公园片区", "朝阳大悦城", "facility", "positive", 0.88,
        "朝阳大悦城地下停车场车位充足，带娃自驾方便。",
        {"facility_tags": ["parking"]})

    for i, evidence in enumerate([
        "玉渊潭公园春季樱花进入花期，适合踏青赏樱。",
        "玉渊潭公园春樱和海棠盛开，春季赏花体验集中。",
        "玉渊潭公园秋季银杏金黄，适合赏叶和拍照。",
        "玉渊潭公园深秋银杏进入观赏期，秋景层次丰富。",
    ], 1):
        add(f"ugc_season_yyt_{i}", "玉渊潭片区", "玉渊潭公园", "seasonal", "positive", 0.86,
            evidence, {"seasonal_fixture": True}, time_bucket="general", intensity=0.55)

    # Repeated wait samples for histogram-driven tools.
    for i, mins in enumerate([120, 150, 180, 210, 240], 1):
        add(f"ugc_wait_huda_{i}", "什刹海-鼓楼片区", "胡大饭馆(簋街总店)", "queue", "negative", 0.86,
            f"胡大饭馆周五晚排队 {mins // 60}-{max(mins // 60 + 1, 3)} 小时，等位很长。",
            {"risk_tags": ["long_wait"], "queue_wait_min": mins}, time_bucket="evening", intensity=0.30)
    for i, mins in enumerate([60, 75, 90, 60, 80], 1):
        add(f"ugc_wait_qjd_wfj_{i}", "王府井-东单片区", "全聚德烤鸭（王府井店）", "queue", "negative", 0.84,
            f"全聚德烤鸭（王府井店）饭点排队 {mins} 分钟，游客打卡多。",
            {"risk_tags": ["long_wait"], "queue_wait_min": mins})
    for i, mins in enumerate([60, 90, 75, 90, 120], 1):
        add(f"ugc_wait_qjd_qm_{i}", "天安门-故宫片区", "全聚德前门店", "queue", "negative", 0.85,
            f"全聚德前门店周末排队 {mins} 分钟，老字号总店但等位明显。",
            {"risk_tags": ["long_wait", "heritage_brand"], "queue_wait_min": mins})

    stable_wait_samples = [
        (_pick_food_name(pois, "五道营-雍和宫片区", "方砖厂69号炸酱面(雍和宫店)"),
         "五道营-雍和宫片区", [70, 80, 90]),
        (_pick_food_name(pois, "王府井-东单片区", "全聚德烤鸭（王府井店）"),
         "王府井-东单片区", [55, 65, 75]),
        (_pick_food_name(pois, "什刹海-鼓楼片区", "胡大饭馆(簋街总店)"),
         "什刹海-鼓楼片区", [50, 60, 70]),
        (_pick_food_name(pois, "天安门-故宫片区", "全聚德前门店"),
         "天安门-故宫片区", [45, 60, 75]),
        (_pick_food_name(pois, "三里屯片区", "三里屯太古里"),
         "三里屯片区", [40, 55, 70]),
    ]
    for poi_name, area, waits in stable_wait_samples:
        for j, mins in enumerate(waits, 1):
            add(f"ugc_wait_stable_{poi_name}_{j}", area, poi_name, "queue", "negative", 0.82,
                f"{poi_name} 周末饭点排队 {mins} 分钟，等位样本稳定。",
                {"risk_tags": ["long_wait"], "queue_wait_min": mins})

    filler_pois = [
        p for p in pois
        if p.get("category_lv1") == "餐饮服务"
    ]
    dataset_versions = [
        "manual_ugc_seed_v1",
        "synthetic_from_public_summaries_v2",
        "derived_from_amap_attributes_v2",
        "synthetic_from_scenario_theme_v2",
    ]
    scenario_anchors = [f"scenario:demo_{i:02d}" for i in range(12)]
    theme_anchors = [f"theme:mock_theme_{i:02d}" for i in range(10)]
    extra_anchors = list(AREA_CENTERS) + scenario_anchors + theme_anchors + [f"mock_area_{i:02d}" for i in range(40)]

    for i in range(1120 if filler_pois else 0):
        poi = filler_pois[i % len(filler_pois)]
        wait = 20 + (i % 8) * 10
        if i < 360:
            evidence = f"{poi['name']} 周末排队 {wait} 分钟，等位可控，适合吃饭后继续逛街。"
            aspect = "queue"
            sentiment = "negative" if i % 4 == 0 else "mixed"
            norm = {"risk_tags": ["queue"], "queue_wait_min": wait, "scene_tags": ["meal"]}
        elif i % 5 == 0:
            evidence = f"{poi['name']} 本地街坊常去，卫生间方便，母婴室和推车动线友好，适合带娃。"
            aspect = "comfort"
            sentiment = "positive"
            norm = {"scene_tags": ["family_meal"], "facility_tags": ["toilet", "baby"]}
        elif i % 5 == 1:
            evidence = f"{poi['name']} 周末下午逛街顺路，咖啡和甜品稳定，适合朋友聊天。"
            aspect = "scenario_fit"
            sentiment = "positive"
            norm = {"fit_scores": {"coffee": 0.7, "friends_gathering": 0.65}, "scene_tags": ["coffee", "chat"]}
        elif i % 5 == 2:
            evidence = f"{poi['name']} 夏季暴晒时室内座位舒服，冬季供暖足，插座充电方便。"
            aspect = "environment"
            sentiment = "positive"
            norm = {"scene_tags": ["indoor"], "facility_tags": ["charging"]}
        elif i % 5 == 3:
            evidence = f"{poi['name']} 停车位少，周末下午停车困难，建议地铁或步行。"
            aspect = "transport"
            sentiment = "negative"
            norm = {"risk_tags": ["parking_hard"]}
        else:
            evidence = f"{poi['name']} 老北京口味轻，低油菜和不辣选项多，人均价格稳定。"
            aspect = "food"
            sentiment = "positive"
            norm = {"taste_tags": ["light_diet", "no_spicy"]}

        area_anchor = extra_anchors[i % len(extra_anchors)]
        if i % 11 == 0:
            area_anchor = scenario_anchors[i % len(scenario_anchors)]
        elif i % 13 == 0:
            area_anchor = theme_anchors[i % len(theme_anchors)]

        add(
            f"ugc_mock_{i:04d}",
            area_anchor,
            poi["name"],
            aspect,
            sentiment,
            0.62 + (i % 35) / 100,
            evidence,
            norm,
            time_bucket=["weekend_afternoon", "general", "evening", "weekday_dinner"][i % 4],
            intensity=[0.9, 0.55, 0.25, 0.35][i % 4],
            dataset_version=dataset_versions[i % len(dataset_versions)],
        )

    return rows


def _pick_food_name(pois: list[dict], area_anchor: str, fallback: str) -> str:
    for poi in pois:
        if poi.get("category_lv1") != "餐饮服务":
            continue
        if _poi_matches_area(poi, area_anchor):
            return poi["name"]
    return fallback


def _poi_matches_area(poi: dict, area_anchor: str) -> bool:
    raw = poi.get("raw_poi") or {}
    if raw.get("source_area_anchor") == area_anchor:
        return True
    area_short = area_anchor.replace("片区", "")
    business_area = poi.get("business_area") or ""
    return bool(area_short and (area_short in business_area or business_area in area_short))


def _build_routes(pois: list[dict], *, estimated: bool, pair_limit: int) -> list[dict]:
    modes = {
        "walking": 5.0,
        "bicycling": 15.0,
        "driving": 25.0,
        "transit": 18.0,
    }
    route_pois = [p for p in pois if p["longitude"] and p["latitude"]][:80]
    pairs: list[tuple[dict, dict]] = []
    for i, origin in enumerate(route_pois):
        for j in range(i + 1, min(i + 8, len(route_pois))):
            pairs.append((origin, route_pois[j]))
            if len(pairs) >= pair_limit:
                break
        if len(pairs) >= pair_limit:
            break

    rows: list[dict] = []
    prefix = "est" if estimated else "amap"
    for origin, dest in pairs:
        dist_m = max(120, int(_distance_km(origin, dest) * 1.3 * 1000))
        for mode, speed in modes.items():
            duration_s = int((dist_m / 1000) / speed * 3600)
            if mode == "transit":
                duration_s += 300
            rows.append({
                "cache_key": f"{prefix}:{origin['provider_poi_id']}->{dest['provider_poi_id']}:{mode}",
                "scene_id": f"{prefix}:{origin['business_area']}--{dest['business_area']}",
                "leg_id": f"{origin['name']}--{dest['name']}",
                "mode": mode,
                "summary": {
                    "distance_m": dist_m,
                    "duration_s": max(60, duration_s),
                    "summary": f"{mode} mock {dist_m}m {max(1, round(duration_s / 60))}min",
                },
                "request": {
                    "params": {
                        "origin": f"{origin['longitude']},{origin['latitude']}",
                        "destination": f"{dest['longitude']},{dest['latitude']}",
                    }
                },
                "dataset_version": "estimated_v2_haversine" if estimated else "mock_amap_cache_v1",
                "method": "mock_haversine_x_detour",
                "from_poi": origin["name"],
                "to_poi": dest["name"],
                "from_business_area": origin["business_area"],
                "to_business_area": dest["business_area"],
            })
    return rows


def _distance_km(a: dict, b: dict) -> float:
    lng1, lat1 = math.radians(float(a["longitude"])), math.radians(float(a["latitude"]))
    lng2, lat2 = math.radians(float(b["longitude"])), math.radians(float(b["latitude"]))
    dlng = lng2 - lng1
    dlat = lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("demo", "real-cache", "auto"),
        default="demo",
        help="demo is public and deterministic; real-cache requires a local AMap cache",
    )
    args = parser.parse_args()
    main(args.profile)
