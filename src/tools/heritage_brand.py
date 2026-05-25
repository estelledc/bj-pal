"""老字号品牌识别 + 真假分店区分（[08] 改进点）。

加载 data/heritage_brands.json，提供：
- identify_brand(poi_name) → BrandInfo | None
- score_branch_quality(poi) → float ∈ [0, 1]：分店质量分（看是否总店 + 评分 + 命名）
- is_heritage_brand_query(text) → bool：用户文本是否在求老字号
- filter_heritage_pois(pois, brand) → list[POI]：按老字号关键词过滤候选

为什么需要？
- "全聚德" 在北京有 30+ 分店，质量差距巨大（前门和平门店 4.7 / 个别 3.x）
- 游客一搜"全聚德"踩雷概率高
- 当前 ranking 只用 amap rating，老字号分店评分不一定能反映本店权威度
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "heritage_brands.json"

_BRANDS_CACHE: Optional[dict] = None


def _load() -> dict:
    global _BRANDS_CACHE
    if _BRANDS_CACHE is not None:
        return _BRANDS_CACHE
    with DATA_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    _BRANDS_CACHE = data["brands"]
    logger.info(f"[heritage_brand] loaded {len(_BRANDS_CACHE)} brands")
    return _BRANDS_CACHE


# ============================================================
# Brand identification
# ============================================================

@dataclass
class BrandInfo:
    brand: str                 # canonical brand name
    founded_year: int
    type: str
    category: str              # 中华老字号 / 北京老字号 / 京味地标小吃
    is_flagship: bool          # 该 POI 是否是总店 / 老店
    flagship_locations: list[str] = field(default_factory=list)
    branch_min_acceptable_rating: float = 4.0
    notes: str = ""
    warning: str = ""


def identify_brand(poi_name: str) -> Optional[BrandInfo]:
    """识别 POI 名是否属于老字号；并判断是否总店。

    Args:
        poi_name: amap POI 名（"全聚德(南锣鼓巷店)"）

    Returns:
        BrandInfo if 命中老字号；否则 None
    """
    if not poi_name:
        return None
    brands = _load()
    for canonical, raw in brands.items():
        if canonical in poi_name:
            is_flagship = _is_flagship_branch(poi_name, canonical, raw)
            return BrandInfo(
                brand=canonical,
                founded_year=raw.get("founded_year", 0),
                type=raw.get("type", ""),
                category=raw.get("category", ""),
                is_flagship=is_flagship,
                flagship_locations=raw.get("flagship_locations", []),
                branch_min_acceptable_rating=raw.get("branch_min_acceptable_rating", 4.0),
                notes=raw.get("notes", ""),
                warning=raw.get("warning", ""),
            )
    return None


def _is_flagship_branch(poi_name: str, canonical: str, raw: dict) -> bool:
    """判断 POI 是否是该品牌的总店 / 老店 / 旗舰店。

    规则（按优先级）：
    1. POI 名含 raw['flagship_keywords'] 任一关键词（"总店"、"老店"等）
    2. POI 名含 raw['flagship_locations'] 任一地名（"前门店"、"大栅栏店"）
    3. POI 名 == 品牌名本身（无后缀，如"东来顺饭庄"无分店标识）
    """
    name_lower = poi_name
    for kw in raw.get("flagship_keywords", []):
        if kw and kw in name_lower:
            return True
    for loc in raw.get("flagship_locations", []):
        if loc and loc in name_lower:
            return True
    # 无 "(.*店)" 后缀 → 视为本店
    if not re.search(r"[（(].+[）)]", poi_name):
        return True
    return False


def score_branch_quality(poi) -> float:
    """[0, 1] 分店质量评分。

    score 组成：
    - is_flagship → 1.0
    - 非总店但 rating ≥ branch_min_acceptable_rating → 0.7
    - 评分稍低 → 0.4-0.6
    - 评分明显低于品牌底线（< -0.3）→ 0.2-0.3
    - 不是老字号 → 0.5（中性，不加不减）
    """
    name = getattr(poi, "name", None) or (poi.get("name") if isinstance(poi, dict) else None)
    rating = getattr(poi, "rating", None)
    if rating is None and isinstance(poi, dict):
        rating = poi.get("rating")
    if not name:
        return 0.5

    info = identify_brand(name)
    if info is None:
        return 0.5  # 非老字号不参与

    if info.is_flagship:
        return 1.0
    if rating is None:
        return 0.6  # 缺数据偏中性
    cap = info.branch_min_acceptable_rating
    if rating >= cap:
        return 0.7
    if rating >= cap - 0.3:
        return 0.5
    return 0.3


# ============================================================
# Heritage query detection + filtering
# ============================================================

# 老字号关键词（用户表达"想吃地道老字号"时触发）
_HERITAGE_QUERY_KEYWORDS = [
    "老字号", "老店", "传统", "京味", "北京菜", "正宗",
    "百年", "中华老字号", "本地特色", "地道",
]


def is_heritage_brand_query(text: str) -> bool:
    """用户文本是否在表达"想体验老字号"。"""
    if not text:
        return False
    return any(kw in text for kw in _HERITAGE_QUERY_KEYWORDS)


def filter_heritage_pois(pois: list, only_flagship: bool = True) -> list:
    """从候选 POI 列表里只保留老字号；可选只留总店。

    Args:
        pois: POI 列表（dataclass 或 dict）
        only_flagship: True → 只保留 is_flagship=True 的分店

    Returns:
        过滤后的 POI 列表
    """
    out = []
    for p in pois:
        name = getattr(p, "name", None) or (p.get("name") if isinstance(p, dict) else None)
        if not name:
            continue
        info = identify_brand(name)
        if info is None:
            continue
        if only_flagship and not info.is_flagship:
            continue
        # 评分黑名单：非总店且评分低于品牌底线 → 剔除
        rating = getattr(p, "rating", None)
        if rating is None and isinstance(p, dict):
            rating = p.get("rating")
        if rating is not None and rating < info.branch_min_acceptable_rating - 0.5 and not info.is_flagship:
            continue
        out.append(p)
    return out


def list_known_brands() -> list[str]:
    return list(_load().keys())


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    print(f"=== 加载 {len(_load())} 个老字号 ===\n")

    # 真实 amap POI 名识别
    cases = [
        ("全聚德(南锣鼓巷店)", False),                    # 分店
        ("全聚德前门店", True),                            # 总店关键词
        ("东来顺饭庄(前门大街店)", True),                  # 总店命名
        ("东来顺东小馆清真砂锅", True),                    # 无 (店) 后缀
        ("北京稻香村鼓楼店", True),                        # 总店地点
        ("北京三禾稻香村", True),                          # 无后缀
        ("聚宝源(王府井二店)", False),                     # 分店
        ("张一元茶庄(总店)", True),                        # 含"总店"
        ("吴裕泰茶庄(王府井店)", True),                    # 王府井是 flagship_locations
        ("六必居(前门店)", True),                          # 前门
        ("某神秘咖啡馆", None),                            # 非老字号
    ]
    print("=== identify_brand ===")
    for name, expected_flagship in cases:
        info = identify_brand(name)
        if info is None:
            actual = None
            ok = "✓" if expected_flagship is None else "✗"
            print(f"  {ok} {name:30s} → 非老字号")
        else:
            actual = info.is_flagship
            ok = "✓" if actual == expected_flagship else "✗"
            print(f"  {ok} {name:30s} → {info.brand} ({info.category}) flagship={actual}")

    # heritage query
    print("\n=== is_heritage_brand_query ===")
    queries = [
        ("想吃老字号烤鸭", True),
        ("找个百年老店", True),
        ("我要吃日料", False),
        ("地道京味", True),
        ("现代咖啡馆", False),
    ]
    for q, ex in queries:
        ok = "✓" if is_heritage_brand_query(q) == ex else "✗"
        print(f"  {ok} {q!r} → {is_heritage_brand_query(q)}")

    # score branch quality
    print("\n=== score_branch_quality ===")

    class FakePoi:
        def __init__(self, name, rating):
            self.name = name
            self.rating = rating

    samples = [
        FakePoi("全聚德前门店", 4.7),                   # flagship → 1.0
        FakePoi("全聚德(南锣鼓巷店)", 4.3),             # 分店刚好达底线
        FakePoi("某老字号假分店", 3.8),                  # 不识别 → 0.5
        FakePoi("便宜坊(灯市口店)", 4.7),                # 含 flagship_location → 1.0
    ]
    for p in samples:
        s = score_branch_quality(p)
        info = identify_brand(p.name)
        brand = info.brand if info else "（非老字号）"
        print(f"  {p.name:25s} rating={p.rating} → score={s:.2f}  brand={brand}")
