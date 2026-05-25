"""L2 time_bucket 5 case — 4 时段 + 重排效果。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.amap_search import resolve_area_center, search_pois  # noqa: E402
from tools.rank_fuse import fuse_and_rank  # noqa: E402
from tools.time_bucket import detect_time_bucket, score_poi_for_bucket  # noqa: E402
from tools.types import SearchConstraints  # noqa: E402


def _detect_runner(query: str, expect_bucket: str):
    def _r() -> dict:
        t0 = time.perf_counter()
        d = detect_time_bucket(query)
        elapsed = int((time.perf_counter() - t0) * 1000)
        return {
            "pass": d.bucket == expect_bucket,
            "observed": {"got": d.bucket, "expected": expect_bucket,
                         "confidence": d.confidence, "evidence": d.evidence},
            "latency_ms": elapsed,
        }
    return _r


def _ranking_changes_runner():
    """friday_night context 应让烤鸭/涮肉进 top5。"""
    def _r() -> dict:
        t0 = time.perf_counter()
        c = SearchConstraints(persona="friends", min_rating=4.0,
                              walk_radius_km=2.0, budget_per_person=300)
        pois = search_pois(area_anchor="五道营-雍和宫片区",
                           category="food", constraints=c, limit=20)
        center = resolve_area_center("五道营-雍和宫片区")
        r_fn = fuse_and_rank(pois, c, center=center, time_context="friday_night")
        top5 = " ".join(r.poi.name for r in r_fn[:5])
        ok = any(kw in top5 for kw in ["烤", "涮", "火锅"])
        elapsed = int((time.perf_counter() - t0) * 1000)
        return {
            "pass": ok,
            "observed": {"top5": [r.poi.name for r in r_fn[:5]],
                         "matched_keywords": [kw for kw in ["烤", "涮", "火锅"] if kw in top5]},
            "latency_ms": elapsed,
        }
    return _r


CASES = [
    {
        "name": "tb1_friday_night_keyword",
        "capability": "time_bucket",
        "description": "「周五下班去簋街」→ friday_night",
        "runner": _detect_runner("周五下班去簋街吃烧烤", "friday_night"),
    },
    {
        "name": "tb2_rainy_indoor",
        "capability": "time_bucket",
        "description": "「下大雨想找室内」→ rainy_indoor",
        "runner": _detect_runner("下大雨想找室内的地方", "rainy_indoor"),
    },
    {
        "name": "tb3_holiday_morning",
        "capability": "time_bucket",
        "description": "「春节大年初二带爹妈逛庙会」→ holiday_morning",
        "runner": _detect_runner("春节大年初二带爹妈逛庙会", "holiday_morning"),
    },
    {
        "name": "tb4_weekend_default",
        "capability": "time_bucket",
        "description": "「周六下午」→ weekend_afternoon",
        "runner": _detect_runner("周六下午带娃溜达", "weekend_afternoon"),
    },
    {
        "name": "tb5_friday_night_reranks_food",
        "capability": "time_bucket",
        "description": "friday_night context 让 food top5 含烤鸭/涮肉/火锅",
        "runner": _ranking_changes_runner(),
    },
]
