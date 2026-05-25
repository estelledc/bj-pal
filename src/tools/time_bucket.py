"""v2.6 D4 时段扩展 — 时段画像识别 + POI 启发式打分。

之前 v2.2 加了 weekend_afternoon_intensity 列覆盖周六下午场景。
v2.6 在代码层扩展到 4 个时段，不动数据 schema：

| time_bucket          | 触发关键词                   | 加分 POI 类                        |
|----------------------|------------------------------|------------------------------------|
| weekend_afternoon    | 周六/周日下午（默认）        | 老字号餐厅 + 文化场所 + 胡同       |
| friday_night         | 周五晚 / 下班后             | 酒吧 / 烤肉 / 夜市 / 火锅          |
| rainy_indoor         | 雨天 / 下雨 / 室内            | 博物馆 / 书店 / 商场 / 咖啡馆      |
| holiday_morning      | 春节庙会 / 早间假期           | 早茶 / 庙会 / 公园早场             |

不依赖新 SQLite 列，纯启发式（POI 名 / category / typecode 关键词）。
后续 v2.7 真要做"周五晚 intensity"列时复用此模块的 score 函数当 ground truth。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from .types import POI

TimeBucket = Literal[
    "weekend_afternoon",   # 默认
    "friday_night",
    "rainy_indoor",
    "holiday_morning",
    "none",                # 完全识别不到时段信号
]


# ============================================================
# 检测部分：query / target_dt → time_bucket
# ============================================================

FRIDAY_NIGHT_KEYWORDS = [
    "周五晚", "周五下班", "周五夜",
    "下班后", "下班吃", "晚上聚",
    "夜里", "夜场",
]
RAINY_KEYWORDS = [
    "雨天", "下雨", "雷雨", "阵雨", "下雨天", "暴雨",
    "潮湿", "刮风下雨",
]
HOLIDAY_MORNING_KEYWORDS = [
    "庙会", "春节", "大年初", "初一", "初二", "初三", "初四", "初五",
    "假期早", "假期上午", "节日早",
    "早茶", "早场", "上午",
]
INDOOR_KEYWORDS = [
    "室内", "屋里", "不在外面", "找个屋",
]
WEEKEND_AFTERNOON_KEYWORDS = [
    "周六下午", "周日下午", "周末下午",
    "礼拜六下午", "礼拜天下午",
]


@dataclass
class TimeBucketDetection:
    bucket: TimeBucket
    signals: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence: str = ""


def detect_time_bucket(
    query: str,
    target_dt: Optional[datetime] = None,
) -> TimeBucketDetection:
    """从 query 文本 + 可选当前时间识别 time_bucket。

    优先级：友好关键词命中 > 当前时间推断（如果都没命中 → bucket=none）

    target_dt: 用户指定的活动开始时间。例如 prefs.target_start
               解析后传进来，可让 friday_night 在没文字关键词时也能命中。
    """
    text = query or ""
    signals: list[str] = []

    # 1) 关键词命中（最强信号）
    fn = next((kw for kw in FRIDAY_NIGHT_KEYWORDS if kw in text), "")
    if fn:
        signals.append(f"keyword:{fn}")
        return TimeBucketDetection(
            bucket="friday_night",
            signals=signals,
            confidence=0.85,
            evidence=f"识别到「{fn}」→ 切到周五晚画像",
        )

    rainy = next((kw for kw in RAINY_KEYWORDS if kw in text), "")
    indoor = next((kw for kw in INDOOR_KEYWORDS if kw in text), "")
    if rainy or (indoor and not _has_weekend_afternoon(text)):
        signals.append(f"keyword:{rainy or indoor}")
        return TimeBucketDetection(
            bucket="rainy_indoor",
            signals=signals,
            confidence=0.80 if rainy else 0.60,
            evidence=f"识别到「{rainy or indoor}」→ 切到室内/雨天画像",
        )

    hm = next((kw for kw in HOLIDAY_MORNING_KEYWORDS if kw in text), "")
    if hm:
        signals.append(f"keyword:{hm}")
        return TimeBucketDetection(
            bucket="holiday_morning",
            signals=signals,
            confidence=0.75,
            evidence=f"识别到「{hm}」→ 切到假期早间画像",
        )

    wa = next((kw for kw in WEEKEND_AFTERNOON_KEYWORDS if kw in text), "")
    if wa:
        signals.append(f"keyword:{wa}")
        return TimeBucketDetection(
            bucket="weekend_afternoon",
            signals=signals,
            confidence=0.90,
            evidence=f"识别到「{wa}」→ 默认周末下午画像",
        )

    # 2) target_dt 推断（次强信号）
    if target_dt is not None:
        wd = target_dt.weekday()  # 0=周一, 4=周五, 5=周六, 6=周日
        h = target_dt.hour
        if wd == 4 and h >= 18:
            signals.append(f"datetime:周五{h}时")
            return TimeBucketDetection(
                bucket="friday_night",
                signals=signals,
                confidence=0.70,
                evidence=f"target_dt 周五 {h}:00 → 周五晚",
            )
        if wd in (5, 6) and 12 <= h <= 18:
            signals.append(f"datetime:周末{h}时")
            return TimeBucketDetection(
                bucket="weekend_afternoon",
                signals=signals,
                confidence=0.85,
                evidence=f"target_dt 周末 {h}:00 → 周末下午",
            )
        if 5 <= h <= 11:
            signals.append(f"datetime:上午{h}时")
            return TimeBucketDetection(
                bucket="holiday_morning",
                signals=signals,
                confidence=0.55,
                evidence=f"target_dt 上午 {h}:00 → 早间画像",
            )

    return TimeBucketDetection(
        bucket="none",
        signals=signals,
        confidence=0.0,
        evidence="无明确时段信号",
    )


def _has_weekend_afternoon(text: str) -> bool:
    return any(kw in text for kw in WEEKEND_AFTERNOON_KEYWORDS)


# ============================================================
# 打分部分：POI × time_bucket → (delta, evidence)
# ============================================================

# 每个时段对哪些 POI 关键词加分 / 减分
BUCKET_SCORES: dict[str, dict[str, list[str]]] = {
    "friday_night": {
        "boost": ["酒吧", "烤肉", "夜市", "火锅", "簋街", "酒馆", "ktv",
                  "啤酒", "精酿", "夜场", "宵夜"],
        "demote": ["博物馆", "书店", "图书馆", "美术馆", "早茶"],
    },
    "rainy_indoor": {
        "boost": ["博物馆", "书店", "图书馆", "美术馆", "商场", "购物中心",
                  "咖啡", "茶馆", "电影院", "室内"],
        "demote": ["公园", "胡同", "广场", "户外", "湖", "山", "登"],
    },
    "weekend_afternoon": {
        "boost": ["老字号", "胡同", "茶馆", "甜品", "咖啡", "公园"],
        "demote": ["酒吧", "夜市", "ktv", "宵夜"],
    },
    "holiday_morning": {
        "boost": ["庙会", "早茶", "公园", "早餐", "豆汁", "包子", "粥",
                  "古迹", "寺", "庙"],
        "demote": ["酒吧", "夜市", "ktv", "宵夜", "夜场"],
    },
    "none": {"boost": [], "demote": []},
}

BOOST_DELTA = 0.20
DEMOTE_DELTA = -0.18


def _poi_text_blob(poi: POI) -> str:
    parts = [
        poi.name or "",
        poi.category_lv1 or "",
        poi.category_lv2 or "",
        poi.category_lv3 or "",
    ]
    return " ".join(parts).lower()


def score_poi_for_bucket(poi: POI, bucket: str) -> tuple[float, str]:
    """单 POI 在某时段的 ranking delta。

    Returns:
        (delta, evidence) — delta ∈ [-0.18, +0.20]，evidence 是给 reasons 的字符串
    """
    if bucket == "none" or bucket not in BUCKET_SCORES:
        return 0.0, ""

    blob = _poi_text_blob(poi)
    rules = BUCKET_SCORES[bucket]

    boost_hits = [kw for kw in rules["boost"] if kw.lower() in blob]
    demote_hits = [kw for kw in rules["demote"] if kw.lower() in blob]

    if boost_hits:
        return BOOST_DELTA, f"{bucket} 画像加分（命中 {','.join(boost_hits[:2])}）"
    if demote_hits:
        return DEMOTE_DELTA, f"{bucket} 画像减分（{','.join(demote_hits[:2])} 与场景不符）"
    return 0.0, ""


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    # detect 路径
    cases = [
        ("周五下班后跟同事喝点", "friday_night"),
        ("下雨天找个屋里待着", "rainy_indoor"),
        ("春节大年初二带爹妈逛庙会", "holiday_morning"),
        ("周六下午带娃溜达", "weekend_afternoon"),
        ("4 人吃饭", "none"),
    ]
    for q, expect in cases:
        d = detect_time_bucket(q)
        assert d.bucket == expect, f"{q} → {d.bucket} ≠ {expect}"
        print(f"✓ detect: {q!r} → {d.bucket} (conf={d.confidence})")

    # target_dt 路径
    fri_night = datetime(2026, 6, 5, 19, 0)  # 2026-06-05 是周五
    d = detect_time_bucket("出去吃个饭", target_dt=fri_night)
    assert d.bucket == "friday_night", d.bucket
    print(f"✓ datetime fri 19h → {d.bucket}")

    # score 路径
    poi_bar = POI(
        id="b1", name="提督酒吧", category_lv1="休闲娱乐",
        category_lv2="酒吧", category_lv3=None, typecode=None,
        district=None, business_area=None, address=None,
        longitude=None, latitude=None, rating=4.5, avg_price=200,
        open_time=None, phone=None, photos=[],
    )
    d_score, ev = score_poi_for_bucket(poi_bar, "friday_night")
    assert d_score > 0, (d_score, ev)
    print(f"✓ score 酒吧/friday_night: +{d_score} 「{ev}」")

    d_score, ev = score_poi_for_bucket(poi_bar, "holiday_morning")
    assert d_score < 0
    print(f"✓ score 酒吧/holiday_morning: {d_score} 「{ev}」")

    poi_park = POI(
        id="p1", name="地坛公园", category_lv1="风景名胜",
        category_lv2="公园广场", category_lv3=None, typecode=None,
        district=None, business_area=None, address=None,
        longitude=None, latitude=None, rating=4.7, avg_price=None,
        open_time=None, phone=None, photos=[],
    )
    d_score, ev = score_poi_for_bucket(poi_park, "rainy_indoor")
    assert d_score < 0, (d_score, ev)
    print(f"✓ score 公园/rainy_indoor: {d_score} 「{ev}」")

    print("\n所有 time_bucket 自测通过！")
