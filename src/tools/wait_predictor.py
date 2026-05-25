"""等位时长 UGC 直方图预测（[39] 改进点）。

从 6300 UGC 抽 "等了 X 分钟"、"排队 1-2 小时" 等数字，
按 (poi_name, time_bucket) 聚合成直方图，预测真实排队时长。

为什么不用启发式？
- 当前 availability_probe 用 amap rating 启发式给排队风险，太粗
- "全聚德排队 1-2 小时" 这种 UGC 里其实有真实分钟数，可被结构化
- 305/6300 UGC 含分钟数，覆盖足够支撑 demo 必跑 POI

抽取规则（按精度降序）：
- "排队 X-Y 分钟" / "排队 X-Y 小时" → 取中点
- "等位 X 分钟" / "等 X min"
- "排队 X 小时" / "X-Y 小时"
- 单纯出现 X 分钟（默认归为等位时长）

输出：
- predict_wait(poi_name) → {expected_min, p50, p90, n_samples, confidence}
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


# ============================================================
# 抽取规则
# ============================================================

# 注意：顺序很重要，先匹配长格式（小时区间），再退回单个分钟
# 中文数字到阿拉伯（简化版，只覆盖常见）
_CHN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
            "七": 7, "八": 8, "九": 9, "十": 10}


def _chn_to_int(s: str) -> Optional[int]:
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _CHN_NUM:
        return _CHN_NUM[s]
    # 十X / X十
    if "十" in s and len(s) <= 3:
        if s == "十":
            return 10
        if s.startswith("十"):
            return 10 + (_CHN_NUM.get(s[1:], 0))
        if s.endswith("十"):
            return _CHN_NUM.get(s[:-1], 0) * 10
    return None


# 正则按优先级排
_PATTERNS = [
    # "1-2 小时" / "X-Y 小时"
    (re.compile(r"(\d+|[一二两三四五六七八九十]+)\s*[-到至]\s*(\d+|[一二两三四五六七八九十]+)\s*(?:小时|h|hr)"),
     "hour_range"),
    # "45-60 分钟" / "30 至 60 分钟"
    (re.compile(r"(\d+)\s*[-到至]\s*(\d+)\s*(?:分钟|min|分(?!\d))"),
     "min_range"),
    # "X 小时" / "三小时"
    (re.compile(r"(\d+|[一二两三四五六七八九十]+)\s*(?:小时|h(?:r)?(?![a-z]))"),
     "hour"),
    # "X 分钟" / "X min"
    (re.compile(r"(\d+)\s*(?:分钟|min|分(?!\d))"),
     "min"),
    # "排队 X" / "等位 X" — 兜底（前面已被前面规则覆盖时就不走这条）
    (re.compile(r"(?:排队|等位|等候)\s*(\d{1,3})"),
     "min"),
]


def extract_wait_minutes(text: str) -> list[int]:
    """从一段文本抽所有等位时长数字（统一为分钟）。"""
    if not text:
        return []
    found: list[int] = []
    seen_spans: list[tuple[int, int]] = []  # 已被高优先级规则吃掉的位置

    def _overlaps(a, b):
        for s, e in seen_spans:
            if not (b <= s or a >= e):
                return True
        return False

    for pat, kind in _PATTERNS:
        for m in pat.finditer(text):
            if _overlaps(m.start(), m.end()):
                continue
            if kind == "hour_range":
                a, b = _chn_to_int(m.group(1)), _chn_to_int(m.group(2))
                if a and b:
                    found.append(int((a + b) / 2 * 60))
            elif kind == "min_range":
                a, b = int(m.group(1)), int(m.group(2))
                found.append(int((a + b) / 2))
            elif kind == "hour":
                v = _chn_to_int(m.group(1))
                if v:
                    found.append(v * 60)
            elif kind == "min":
                v = int(m.group(1))
                # 过滤明显不合理（>360min 可能是别的语义）
                if 1 <= v <= 360:
                    found.append(v)
            seen_spans.append((m.start(), m.end()))
    return found


# ============================================================
# 直方图构建
# ============================================================

@dataclass
class WaitPrediction:
    poi_name: str
    expected_min: int    # mean
    p50: int             # median
    p90: int             # 第 90 百分位
    n_samples: int       # 多少条 UGC 提及
    confidence: float    # ≥ 5 样本 = 0.8，3-4 = 0.5，1-2 = 0.3，0 = 0.0
    raw_minutes: list[int]


_HISTOGRAM: dict[str, list[int]] = {}


def build_histogram(force_rebuild: bool = False) -> int:
    """扫所有 UGC 建 poi_name → [wait_min ...] 字典。"""
    global _HISTOGRAM
    if _HISTOGRAM and not force_rebuild:
        return len(_HISTOGRAM)

    from loader import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT poi_name, evidence_summary FROM ugc_aspects "
        "WHERE evidence_summary IS NOT NULL AND poi_name IS NOT NULL"
    ).fetchall()
    conn.close()

    hist: dict[str, list[int]] = {}
    for r in rows:
        mins = extract_wait_minutes(r["evidence_summary"])
        if mins:
            hist.setdefault(r["poi_name"], []).extend(mins)

    _HISTOGRAM = hist
    n_pois = len(hist)
    n_total = sum(len(v) for v in hist.values())
    logger.info(f"[wait_predictor] {n_pois} POI 有等位数据，共 {n_total} 条记录")
    return n_pois


# ============================================================
# 预测接口
# ============================================================

def predict_wait(poi_name: str) -> Optional[WaitPrediction]:
    """对单个 POI 预测等位时长。

    Returns:
        None if 没有任何 UGC 提及；否则 WaitPrediction。
    """
    if not _HISTOGRAM:
        build_histogram()

    samples = _HISTOGRAM.get(poi_name)
    if not samples:
        # 模糊匹配：UGC 里 POI 名可能略有差异（"全聚德" vs "全聚德烤鸭"）
        for name, mins in _HISTOGRAM.items():
            if poi_name in name or name in poi_name:
                samples = mins
                break
    if not samples:
        return None

    n = len(samples)
    expected = int(round(mean(samples)))
    p50 = int(median(samples))
    sorted_s = sorted(samples)
    p90 = sorted_s[min(int(n * 0.9), n - 1)]

    if n >= 5:
        confidence = 0.8
    elif n >= 3:
        confidence = 0.5
    elif n >= 1:
        confidence = 0.3
    else:
        confidence = 0.0

    return WaitPrediction(
        poi_name=poi_name,
        expected_min=expected,
        p50=p50,
        p90=p90,
        n_samples=n,
        confidence=confidence,
        raw_minutes=samples,
    )


def is_high_wait_risk(poi_name: str, threshold_min: int = 30) -> bool:
    """快速判定：该 POI 是否预计等位 ≥ threshold_min。"""
    pred = predict_wait(poi_name)
    if pred is None:
        return False
    # confidence ≥ 0.5 才采用 expected；否则看 p90
    if pred.confidence >= 0.5:
        return pred.expected_min >= threshold_min
    return pred.p90 >= threshold_min


def get_top_wait_pois(top_k: int = 10) -> list[WaitPrediction]:
    """全局 ranking：等位最长的 top-k POI（仅考虑 ≥3 样本）。"""
    if not _HISTOGRAM:
        build_histogram()
    preds = []
    for name in _HISTOGRAM:
        p = predict_wait(name)
        if p and p.n_samples >= 3:
            preds.append(p)
    preds.sort(key=lambda p: p.expected_min, reverse=True)
    return preds[:top_k]


# ============================================================
# CLI / 自测
# ============================================================

if __name__ == "__main__":
    # 抽取规则单测
    cases = [
        ("周末排队 1-2 小时", [90]),
        ("等位 45 分钟", [45]),
        ("排队 30-60 分钟", [45]),
        ("常年排队 3 小时", [180]),
        ("等了 20min", [20]),
        ("没数字", []),
        ("排队 1-2 小时是常态", [90]),
        ("两小时内能搞定", [120]),
    ]
    print("=== extract_wait_minutes ===")
    for text, expected in cases:
        got = extract_wait_minutes(text)
        ok = "✓" if got == expected else "✗"
        print(f"  {ok} {text!r} → {got} (expected {expected})")

    # 真实直方图
    print("\n=== build_histogram on 6300 UGC ===")
    n = build_histogram()
    print(f"  {n} POI 有等位数据")

    # top 10 等位最长
    print("\n=== top 10 等位最长 POI ===")
    for p in get_top_wait_pois(top_k=10):
        print(f"  {p.poi_name:30s} expected={p.expected_min}min "
              f"p50={p.p50}min p90={p.p90}min n={p.n_samples} conf={p.confidence:.1f}")

    # 几个常见 POI 查询
    print("\n=== 单查 ===")
    for name in ["胡大饭馆", "全聚德烤鸭（王府井店）", "故宫角楼咖啡", "不存在的 POI"]:
        p = predict_wait(name)
        if p:
            print(f"  {name}: expected {p.expected_min}min (p90 {p.p90}min, n={p.n_samples})")
        else:
            print(f"  {name}: 无数据")
