"""UGC Class B：基于 amap POI 客观属性的 aspect 推理。

输入：amap pois 表的客观字段（rating / avg_price / category / open_time / name）
输出：aspect 同 Class A schema，但 dataset_version = "derived_from_amap_attributes_v2"

关键约束：
- evidence_summary 必须引用 amap 字段，**禁止编造**网友评论 / 排队时间
- 仅推理 amap 数据能支撑的 aspect_type：budget（评分+价格）、food（类目）、
  scenario_fit（评分+价格+营业时间组合）、environment（类目+name 关键词）
- 不推理 queue / crowd（amap 没这数据）

跑法：
    BJ_PAL_LLM=longcat python3 src/etl/batch_amap_inference.py [--areas N] [--per-area N]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.llm_client import LLMClient, get_llm_client  # noqa: E402
from etl.text_aspect_extractor import (  # noqa: E402
    ALLOWED_ASPECT_TYPES, ALLOWED_SENTIMENTS, ALLOWED_TIME_BUCKETS,
)
from loader import get_conn  # noqa: E402

DATASET_VERSION = "derived_from_amap_attributes_v2"
SOURCE_PLATFORM = "amap_attribute_inference"

# 这一类只支持基于 amap 数据可推理的 aspect_type
SUPPORTED_TYPES_B = ["budget", "food", "scenario_fit", "environment", "transport"]

INFER_SYSTEM = """你是 BJ-Pal 的 amap-only aspect 推理助手。给一个北京片区 + 该片区 N 条 POI 的客观属性（来自高德地图）。**仅基于这些客观字段**推理 aspect，**不要编造网友引用 / 排队时间 / 主观评价**。

输入字段含义：
- name: POI 名字（中文）
- category: 高德 3 级类目（如"餐饮服务;咖啡厅"）
- rating: 高德评分（0-5）
- avg_price: 客单价（元 / 人）
- open_time: 营业时间字符串

允许推理的 aspect_type 严格限定 5 类：
- budget: 基于 avg_price 推价格水位（人均 X 元，价格 高 / 中 / 低）
- food: 基于 category 推类目覆盖（如"咖啡馆密集 / 西餐为主"）
- scenario_fit: 基于 rating + avg_price 推适用场景（情侣 / 家庭 / 商务）
- environment: 基于 name + category 推氛围（如名字含"老字号"则传统）
- transport: 基于 POI 集中度推交通便利

evidence_summary 必须类似：
- "本片区 POI 类目分布显示 X 类占比高"
- "评分均值 4.6 + 客单价 180 元，定位中高端"
- "包含若干名字含'胡同'的 POI，推测保留传统街区氛围"

严禁出现：
- "网友评价 / 大众点评显示 / 多条评论提到" → 这些 amap 没有
- "排队 X 小时 / 等位 30 分钟" → amap 不提供
- 具体到某条评论的引用

aspect_type 仅 5 选 1：budget / food / scenario_fit / environment / transport
sentiment：positive / negative / mixed
time_bucket：默认 "general"

输出 JSON schema：
```json
{
  "area_anchor": "<片区名>",
  "aspects": [
    {
      "aspect_type": "<5 选 1>",
      "sentiment": "<3 选 1>",
      "confidence": 0.0-1.0,
      "time_bucket": "general",
      "evidence_summary": "<30-80 字 引用 amap 字段>",
      "poi_name": "<具体 POI 名字 或 片区聚合>",
      "normalized_value": {
        "amap_field_basis": ["rating", "avg_price", "category"],
        "scene_tags": []
      }
    }
  ]
}
```

抽取约束：
- 6 条 POI 输入对应输出 6-10 条 aspect
- 不能因数据不够强行编
- 严格只输出 JSON
"""


def fetch_top_pois_by_area(business_area: str, limit: int = 8) -> list[dict]:
    """从 amap pois 表取 top-rated POI。"""
    conn = get_conn()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT name, category_lv1, category_lv2, category_lv3, "
        "       rating, avg_price, open_time, address "
        "FROM pois "
        "WHERE business_area = ? AND rating >= 4.0 "
        "ORDER BY rating DESC, avg_price DESC "
        "LIMIT ?",
        (business_area, limit),
    ).fetchall()
    conn.close()
    return [
        {
            "name": r[0],
            "category": f"{r[1]};{r[2]};{r[3]}".strip(";").strip(),
            "rating": r[4],
            "avg_price": r[5],
            "open_time": r[6] or "",
            "address": r[7] or "",
        }
        for r in rows
    ]


def fetch_top_pois_by_category(category_lv2: str, limit: int = 8) -> list[dict]:
    """按 category 跨 business_area 取 top POI。用于补充非 area 切片的数据。"""
    conn = get_conn()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT name, category_lv1, category_lv2, category_lv3, "
        "       rating, avg_price, open_time, address, business_area "
        "FROM pois "
        "WHERE category_lv2 = ? AND rating >= 4.5 "
        "ORDER BY rating DESC LIMIT ?",
        (category_lv2, limit),
    ).fetchall()
    conn.close()
    return [
        {
            "name": r[0],
            "category": f"{r[1]};{r[2]};{r[3]}".strip(";").strip(),
            "rating": r[4],
            "avg_price": r[5],
            "open_time": r[6] or "",
            "address": r[7] or "",
            "business_area": r[8] or "",
        }
        for r in rows
    ]


def build_inference_prompt(area_anchor: str, pois: list[dict]) -> str:
    parts = [f"片区：{area_anchor}", f"\nPOI 客观属性（共 {len(pois)} 条）："]
    for i, p in enumerate(pois, 1):
        parts.append(
            f"{i}. {p['name']}\n"
            f"   类目: {p.get('category', '未知')}\n"
            f"   评分: {p.get('rating') or '暂无'}\n"
            f"   人均: {p.get('avg_price') or '暂无'} 元\n"
            f"   营业: {p.get('open_time') or '暂无'}\n"
        )
    parts.append(
        f"\n请基于上述客观属性推理 6-10 条 aspect（aspect_type 仅限 5 类："
        "budget / food / scenario_fit / environment / transport）。"
        "evidence_summary 必须引用 amap 字段，**不要编造网友评论或排队信息**。"
        "严格只输出 JSON。"
    )
    return "\n".join(parts)


def infer_for_area(
    area_anchor: str,
    pois: list[dict],
    client: Optional[LLMClient] = None,
) -> dict:
    """调 LLM 基于 POI 属性推理 aspects。"""
    client = client or get_llm_client()
    prompt = build_inference_prompt(area_anchor, pois)
    resp = client.complete(
        system=INFER_SYSTEM,
        user=prompt,
        json_schema={"infer": True},
        temperature=0.2,
    )
    parsed = resp.parsed
    if not parsed and resp.text:
        s = resp.text.strip()
        if s.startswith("```"):
            s = s[s.find("\n") + 1:]
            if s.endswith("```"):
                s = s[:-3]
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            i, j = s.find("{"), s.rfind("}")
            if i >= 0 and j > i:
                try:
                    parsed = json.loads(s[i:j + 1])
                except json.JSONDecodeError:
                    pass
    if not parsed or "aspects" not in parsed:
        raise RuntimeError(f"LLM 返回不可解析。前 200 字：{(resp.text or '')[:200]}")
    parsed["area_anchor"] = area_anchor

    cleaned = []
    for asp in parsed.get("aspects") or []:
        atype = asp.get("aspect_type", "").strip()
        if atype not in SUPPORTED_TYPES_B:
            continue
        sent = asp.get("sentiment", "mixed").strip()
        if sent not in ALLOWED_SENTIMENTS:
            sent = "mixed"
        tb = asp.get("time_bucket", "general").strip()
        if tb not in ALLOWED_TIME_BUCKETS:
            tb = "general"
        cleaned.append({
            "aspect_type": atype,
            "sentiment": sent,
            "confidence": float(asp.get("confidence") or 0.55),
            "time_bucket": tb,
            "evidence_summary": (asp.get("evidence_summary") or "").strip(),
            "poi_name": (asp.get("poi_name") or area_anchor).strip(),
            "normalized_value": asp.get("normalized_value") or {},
        })
    parsed["aspects"] = cleaned
    return parsed


def persist_b(extracted: dict, source_pois: list[dict]) -> int:
    conn = get_conn()
    inserted = 0
    area = extracted.get("area_anchor", "")
    upload_ts = int(time.time())
    poi_names = [p["name"] for p in source_pois]
    for asp in extracted.get("aspects") or []:
        record_id = f"infer_{upload_ts}_{uuid.uuid4().hex[:6]}"
        nv = asp.get("normalized_value") or {}
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ugc_aspects "
                "(record_id, area_anchor, poi_name, aspect_type, sentiment, "
                " confidence, time_bucket, needs_review, evidence_summary, "
                " normalized_value_json, raw_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record_id, area, asp.get("poi_name", area),
                    asp["aspect_type"],
                    asp["sentiment"],
                    asp["confidence"],
                    asp["time_bucket"],
                    0,
                    asp["evidence_summary"],
                    json.dumps(nv, ensure_ascii=False),
                    json.dumps({
                        "record_id": record_id,
                        "area_anchor": area,
                        "poi_name": asp.get("poi_name", area),
                        "aspect_type": asp["aspect_type"],
                        "sentiment": asp["sentiment"],
                        "confidence": asp["confidence"],
                        "time_bucket": asp["time_bucket"],
                        "evidence_summary": asp["evidence_summary"],
                        "normalized_value": nv,
                        "dataset_version": DATASET_VERSION,
                        "source_platform": SOURCE_PLATFORM,
                        "source_poi_basis": poi_names[:8],
                        "extraction_status": "amap_attribute_inference",
                        "privacy_status": "amap_objective_no_pii",
                    }, ensure_ascii=False),
                ),
            )
            inserted += 1
        except sqlite3.Error:
            continue
    conn.commit()
    conn.close()
    return inserted


def main():
    ap = argparse.ArgumentParser(description="amap 属性推理批量扩 UGC Class B")
    ap.add_argument("--areas", type=int, default=20,
                    help="处理多少个 business_area top（默认 20）")
    ap.add_argument("--offset", type=int, default=0,
                    help="跳过前 N 个 area（用于 Round 2 取 26+ 名）")
    ap.add_argument("--per-area", type=int, default=8,
                    help="每个 area 取多少 POI 喂给 LLM（默认 8）")
    ap.add_argument("--min-pois", type=int, default=8,
                    help="area 最少 POI 数（默认 8）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 抓 top business_area 列表（支持 offset）
    conn = get_conn()
    cur = conn.cursor()
    areas = [
        r[0] for r in cur.execute(
            "SELECT business_area, COUNT(*) AS n "
            "FROM pois WHERE business_area IS NOT NULL "
            "AND business_area != '' AND rating >= 4.0 "
            "GROUP BY business_area HAVING n >= ? "
            "ORDER BY n DESC LIMIT ? OFFSET ?",
            (args.min_pois, args.areas, args.offset),
        ).fetchall()
    ]
    conn.close()

    print(f"=== Class B 计划处理 {len(areas)} 个 business_area ===")
    for a in areas:
        print(f"  {a}")

    if args.dry_run:
        print("\nDry run 完，不调 LLM。")
        return

    print("\n=== 开始批量推理 ===")
    total = 0
    for i, area in enumerate(areas, 1):
        pois = fetch_top_pois_by_area(area, args.per_area)
        if len(pois) < 3:
            print(f"\n[{i}/{len(areas)}] {area}  POI 不足 3 条，跳过")
            continue
        # area_anchor 命名对齐：用 "{business_area}-amap推理片区" 区分手写片区
        area_anchor = f"{area}片区B"
        print(f"\n[{i}/{len(areas)}] {area}  POI={len(pois)}")
        try:
            extracted = infer_for_area(area_anchor, pois)
            n_aspects = len(extracted.get("aspects") or [])
            print(f"  LLM 返回 {n_aspects} 条")
            n = persist_b(extracted, pois)
            print(f"  入库 {n} 条")
            total += n
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}")

    print(f"\n=== Class B 完成 ===")
    print(f"新增：{total} 条")
    conn = get_conn()
    cur = conn.cursor()
    print(f"\n=== 当前 ugc_aspects 总数 ===")
    print(f"  {cur.execute('SELECT COUNT(*) FROM ugc_aspects').fetchone()[0]} 条")
    conn.close()


if __name__ == "__main__":
    main()
