"""L2 text_intake 5 case — 多模态文本抽取的噪声容忍 / 边界。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.text_intake import extract_from_text, merge_into_user_input  # noqa: E402


def _runner_factory(text: str, expect_area: str = "", expect_taste: list[str] | None = None,
                     expect_risk: list[str] | None = None, expect_empty: bool = False,
                     use_llm: bool = False):
    expect_taste = expect_taste or []
    expect_risk = expect_risk or []

    def _r() -> dict:
        t0 = time.perf_counter()
        r = extract_from_text(text, use_llm=use_llm)
        elapsed = int((time.perf_counter() - t0) * 1000)
        if expect_empty:
            ok = r.is_empty()
        else:
            ok = (
                (not expect_area or r.area_anchor == expect_area)
                and all(t in r.taste_tags for t in expect_taste)
                and all(t in r.risk_tags for t in expect_risk)
            )
        return {
            "pass": ok,
            "observed": {
                "area": r.area_anchor, "poi": r.poi_name,
                "taste": r.taste_tags, "risk": r.risk_tags,
                "expected_area": expect_area, "expected_taste": expect_taste,
                "expected_risk": expect_risk,
            },
            "latency_ms": elapsed,
        }
    return _r


CASES = [
    {
        "name": "ti1_clean_article",
        "capability": "text_intake",
        "description": "正文清晰的公众号片段 → 提全 area/taste/risk",
        "runner": _runner_factory(
            "周末去了五道营胡同，雍和宫北边那一片现在很出片。"
            "推荐静水StillWater 的咖啡和马卡龙，环境安静适合带娃看绘本。"
            "建议中午去，下午容易排队。",
            expect_area="五道营-雍和宫片区",
            expect_taste=["coffee", "dessert"],
            expect_risk=["queue_long"],
        ),
    },
    {
        "name": "ti2_short_complaint",
        "capability": "text_intake",
        "description": "短朋友圈吐槽 → 抽到拥挤+排队",
        "runner": _runner_factory(
            "今天三里屯逛了一下午，太挤了，火锅店还排队 1 小时",
            expect_area="三里屯片区",
            expect_taste=["spicy"],
            expect_risk=["queue_long", "crowded"],
        ),
    },
    {
        "name": "ti3_chat_ocr_noise",
        "capability": "text_intake",
        "description": "微信 OCR 含 @ 噪声 → 仍能抽到核心信号",
        "runner": _runner_factory(
            "@王哥：南锣鼓巷有家挺好的咖啡店，环境安静\n"
            "@我：人多吗？\n"
            "@王哥：周末稍微挤一点",
            expect_area="什刹海-鼓楼片区",
            expect_taste=["coffee"],
        ),
    },
    {
        "name": "ti4_non_beijing",
        "capability": "text_intake",
        "description": "非北京内容 → area 为空，下游降级",
        "runner": _runner_factory(
            "上海徐汇区有家咖啡店挺好",
            expect_area="",   # 不应误命中
        ),
    },
    {
        "name": "ti5_merge_preserves_base",
        "capability": "text_intake",
        "description": "merge_into_user_input：base 不丢，外部信号 append",
        "runner": (lambda: lambda: _merge_test())(),
    },
]


def _merge_test() -> dict:
    t0 = time.perf_counter()
    intake = extract_from_text(
        "周日带娃去五道营雍和宫附近遛弯，找个安静的咖啡店",
        use_llm=False,
    )
    merged = merge_into_user_input("4 人下午想出门", intake)
    elapsed = int((time.perf_counter() - t0) * 1000)
    ok = (
        "4 人下午想出门" in merged
        and "五道营" in merged
        and ("coffee" in merged or "kid_friendly" in merged)
    )
    return {
        "pass": ok,
        "observed": {"merged_preview": merged[:200]},
        "latency_ms": elapsed,
    }
