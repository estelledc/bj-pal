"""POI weather_shelter 启发式分类 + 天气降级路线（[16] 改进点）。

把每个 POI 分到 4 档遮蔽程度：
- full_indoor：商场 / 餐厅 / 咖啡馆 / 博物馆 / 书店等纯室内
- covered：含连廊 / 屋檐 / 半室外建筑
- subway_direct：地铁直达的商场 / 综合体
- open：公园 / 胡同 / 露天景点

天气状态（可外部指定 / 启发式推断）：
- clear：晴朗，无影响
- rain：下雨 → open 大幅降权
- snow：下雪 → open 中度降权 + 注意保暖
- heatwave：酷暑（北京 7-8 月）→ open 中度降权
- aqi_high：雾霾红色预警 → open 大幅降权
- cold：严寒（北京 12-2 月 < 0°C）→ open 中度降权

集成 rank_fuse：恶劣天气下户外 -0.20，室内 +0.10，必要时硬过滤户外。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

logger = logging.getLogger(__name__)


WeatherState = Literal["clear", "rain", "snow", "heatwave", "aqi_high", "cold"]
ShelterType = Literal["full_indoor", "covered", "subway_direct", "open"]


# ============================================================
# POI 启发式分类
# ============================================================

# category_lv2 → ShelterType 优先
_LV2_TO_SHELTER: dict[str, ShelterType] = {
    "中餐厅": "full_indoor",
    "外国餐厅": "full_indoor",
    "快餐厅": "full_indoor",
    "餐饮相关场所": "full_indoor",
    "休闲餐饮场所": "full_indoor",
    "咖啡厅": "full_indoor",
    "茶艺馆": "full_indoor",
    "冷饮店": "full_indoor",
    "糕饼店": "full_indoor",
    "服装鞋帽皮具店": "full_indoor",
    "购物相关场所": "full_indoor",
    "专卖店": "full_indoor",
    "便民商店/便利店": "full_indoor",
    "家电电子卖场": "full_indoor",
    "家居建材市场": "full_indoor",
    "商场": "full_indoor",
    "运动场馆": "full_indoor",
    "公园广场": "open",
    "风景名胜": "open",
    "风景名胜相关": "open",
}

# category_lv3 / 名字关键词覆盖（细颗粒度）
_INDOOR_KEYWORDS = [
    "博物馆", "美术馆", "图书馆", "书店", "电影院", "剧院",
    "影院", "音乐厅", "天文馆", "科技馆", "动漫", "海洋馆",
    "购物中心", "百货", "广场(室内)", "购物广场", "商业中心",
    "万达", "SKP", "合生汇", "国贸", "太古里(室内)",
]

_OPEN_KEYWORDS = [
    "公园", "胡同", "广场", "桥", "湖", "山", "河滨",
    "古道", "步道", "山路", "山顶", "草原", "草坪",
    "湿地", "沙滩", "营地", "广场",
]

_COVERED_KEYWORDS = [
    "古镇", "牌楼", "庙", "塔", "亭", "廊", "牌坊",
    "回廊", "连廊", "穹顶", "天棚",
]

_SUBWAY_DIRECT_KEYWORDS = [
    "地铁直达", "地下", "万象城", "国贸商城", "西单大悦城",
    "东方新天地", "华贸中心", "君太百货",
]


def classify_poi(poi) -> ShelterType:
    """把 POI 分到 4 档遮蔽程度。

    顺序：
    1. 先看 name 关键词（最准）
    2. 再看 category_lv2 映射
    3. 默认 open（保守 — 不确定时假设户外）
    """
    name = getattr(poi, "name", None) or (poi.get("name") if isinstance(poi, dict) else "")
    cat_lv1 = getattr(poi, "category_lv1", None) or (poi.get("category_lv1") if isinstance(poi, dict) else "")
    cat_lv2 = getattr(poi, "category_lv2", None) or (poi.get("category_lv2") if isinstance(poi, dict) else "")
    cat_lv3 = getattr(poi, "category_lv3", None) or (poi.get("category_lv3") if isinstance(poi, dict) else "")

    blob_name = (name or "")
    blob_cat = f"{cat_lv2 or ''} {cat_lv3 or ''}"

    # 1) name 关键词
    if any(kw in blob_name for kw in _SUBWAY_DIRECT_KEYWORDS):
        return "subway_direct"
    if any(kw in blob_name for kw in _INDOOR_KEYWORDS):
        return "full_indoor"
    if any(kw in blob_name for kw in _COVERED_KEYWORDS):
        return "covered"
    if any(kw in blob_name for kw in _OPEN_KEYWORDS):
        return "open"

    # 2) category_lv3 / lv2 关键词（次优先）
    if any(kw in blob_cat for kw in _INDOOR_KEYWORDS):
        return "full_indoor"
    if any(kw in blob_cat for kw in _OPEN_KEYWORDS):
        return "open"

    # 3) category_lv2 直接映射
    if cat_lv2 in _LV2_TO_SHELTER:
        return _LV2_TO_SHELTER[cat_lv2]

    # 4) lv1 兜底
    if cat_lv1 == "餐饮服务" or cat_lv1 == "购物服务":
        return "full_indoor"
    if cat_lv1 == "风景名胜":
        return "open"

    # 5) 不确定 → 保守户外
    return "open"


# ============================================================
# 天气状态
# ============================================================

@dataclass
class WeatherContext:
    state: WeatherState = "clear"
    description: str = ""
    severity: float = 0.0  # 0-1，越大越严重


def infer_weather_from_month(month: int) -> WeatherContext:
    """月份启发式推断（无真实 API 时使用）。

    - 7, 8：可能 heatwave / rain（北京暴雨季）
    - 12, 1, 2：可能 cold / snow
    - 3, 5：可能 aqi_high（沙尘）
    - 否则 clear
    """
    if month in (7, 8):
        return WeatherContext(state="heatwave", description="北京盛夏，户外暴晒注意防晒",
                              severity=0.5)
    if month in (12, 1, 2):
        return WeatherContext(state="cold", description="北京寒冬，户外保暖",
                              severity=0.4)
    if month == 3:
        return WeatherContext(state="aqi_high", description="北京沙尘季，注意空气质量",
                              severity=0.3)
    return WeatherContext(state="clear", description="天气晴朗", severity=0.0)


# ============================================================
# 加分 / 减分
# ============================================================

# (weather, shelter) → score_delta
_WEATHER_ADJUST: dict[tuple[WeatherState, ShelterType], float] = {
    # rain：户外大砍
    ("rain", "open"):           -0.30,
    ("rain", "covered"):        -0.10,
    ("rain", "subway_direct"):  +0.10,
    ("rain", "full_indoor"):    +0.15,
    # snow
    ("snow", "open"):           -0.20,
    ("snow", "covered"):        -0.05,
    ("snow", "subway_direct"):  +0.10,
    ("snow", "full_indoor"):    +0.10,
    # heatwave（夏季暴晒）
    ("heatwave", "open"):       -0.20,
    ("heatwave", "covered"):    -0.05,
    ("heatwave", "subway_direct"): +0.05,
    ("heatwave", "full_indoor"):+0.10,
    # aqi_high 雾霾红色
    ("aqi_high", "open"):       -0.30,
    ("aqi_high", "covered"):    -0.10,
    ("aqi_high", "subway_direct"): +0.10,
    ("aqi_high", "full_indoor"):+0.15,
    # cold 严寒
    ("cold", "open"):           -0.15,
    ("cold", "covered"):        +0.0,
    ("cold", "subway_direct"):  +0.10,
    ("cold", "full_indoor"):    +0.10,
}


def get_weather_adjust(
    poi,
    weather: Optional[WeatherContext] = None,
) -> tuple[float, str]:
    """计算 weather 对 POI 的 score 调整 + 一句话解释。"""
    if weather is None or weather.state == "clear":
        return 0.0, ""
    shelter = classify_poi(poi)
    delta = _WEATHER_ADJUST.get((weather.state, shelter), 0.0)
    if delta == 0.0:
        return 0.0, ""
    name = getattr(poi, "name", None) or "POI"
    if delta > 0:
        return delta, f"☂️ {weather.state} 天气下 {name}({shelter}) 是好选择"
    return delta, f"⚠️ {weather.state} 天气下 {name}({shelter}) 体验差，建议改室内"


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    class P:
        def __init__(self, name, lv1="", lv2="", lv3=""):
            self.name = name
            self.category_lv1 = lv1
            self.category_lv2 = lv2
            self.category_lv3 = lv3

    samples = [
        P("故宫博物院", "风景名胜", "风景名胜", "博物馆"),
        P("国贸商城", "购物服务", "商场"),
        P("玉渊潭公园", "风景名胜", "公园广场"),
        P("中关村星巴克", "餐饮服务", "咖啡厅"),
        P("南锣鼓巷", "风景名胜", "风景名胜"),
        P("奥林匹克森林公园", "风景名胜", "公园广场"),
        P("万达广场", "购物服务", "商场"),
    ]
    print("=== 启发式分类 ===")
    for p in samples:
        print(f"  {p.name:20s} → {classify_poi(p)}")

    print("\n=== 月份推断天气 ===")
    for m in [1, 4, 7, 10, 12]:
        w = infer_weather_from_month(m)
        print(f"  {m}月: {w.state} ({w.description})")

    print("\n=== 雨天 score 调整 ===")
    rain = WeatherContext(state="rain", description="下雨", severity=0.7)
    for p in samples:
        d, why = get_weather_adjust(p, rain)
        print(f"  {p.name:20s} delta={d:+.2f}  {why}")
