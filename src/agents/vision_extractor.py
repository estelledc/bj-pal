"""UGC 截图 → 结构化 aspect 抽取（v2 改 6A）。

输入：用户上传的大众点评截图（jpg/png bytes）
输出：1 套 area_anchor + poi_name + 多条 aspect（结构同 manual_ugc_seed.jsonl）

抽取后落入 SQLite ugc_aspects 表（source_platform="user_uploaded"），
重新 ranking 时新 aspects 一起被消费——demo 演"链路通用"。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import get_conn  # noqa: E402

from .llm_client import LLMClient, get_llm_client  # noqa: E402

VISION_SYSTEM = """你是 BJ-Pal UGC 抽取助手。给一张大众点评 / 美团 / 小红书截图，按下列 schema 抽取结构化信号。

schema：
```
{
  "area_anchor": "<五道营-雍和宫片区 / 三里屯片区 / ... 任选合理片区>",
  "poi_name": "<图中提到的店名>",
  "aspects": [
    {
      "aspect_type": "<environment | comfort | food | budget | crowd | transport | queue | scenario_fit | booking_risk>",
      "sentiment": "<positive | negative | mixed>",
      "confidence": 0.0-1.0,
      "evidence_summary": "<30-60 字 用户评价摘要>",
      "normalized_value": {
        "risk_tags": ["..."],
        "scene_tags": ["..."],
        "taste_tags": ["..."]
      }
    }
  ]
}
```

抽取要点：
- 1 张图通常给 2-4 条 aspect
- 没明确证据的字段不要编造
- 只输出 JSON，不要任何其他文字、markdown 包裹

只输出 JSON。
"""


def extract_from_image(
    image_bytes: bytes,
    image_mime: str = "image/jpeg",
    client: Optional[LLMClient] = None,
) -> dict:
    """调 vision LLM 从截图抽取结构化 aspects。

    返回：{area_anchor, poi_name, aspects: [...]}
    失败时抛异常；调用方负责 try/except。
    """
    client = client or get_llm_client()
    resp = client.vision_complete(
        system=VISION_SYSTEM,
        user="请按 schema 抽取这张截图中的结构化信号。只输出 JSON。",
        image_bytes=image_bytes,
        image_mime=image_mime,
        json_schema={"extract": True},
        temperature=0.2,
    )
    parsed = resp.parsed
    if not parsed and resp.text:
        # 容错：尝试直接 json.loads
        try:
            s = resp.text.strip()
            if s.startswith("```"):
                s = s[s.find("\n") + 1:]
                if s.endswith("```"):
                    s = s[:-3]
            parsed = json.loads(s)
        except json.JSONDecodeError:
            pass
    if not parsed or "aspects" not in parsed:
        raise RuntimeError(f"vision 返回不可解析。前 200 字：{resp.text[:200]}")
    return parsed


def persist_to_db(
    extracted: dict,
    source_platform: str = "user_uploaded",
) -> int:
    """把 extract_from_image 的结果落 SQLite ugc_aspects 表。返回新增条数。"""
    conn = get_conn()
    inserted = 0
    area = extracted.get("area_anchor") or "用户上传"
    poi_name = extracted.get("poi_name") or "未知 POI"
    upload_ts = int(time.time())
    for asp in extracted.get("aspects") or []:
        record_id = f"upload_{upload_ts}_{uuid.uuid4().hex[:6]}"
        nv = asp.get("normalized_value") or {}
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ugc_aspects "
                "(record_id, area_anchor, poi_name, aspect_type, sentiment, "
                " confidence, time_bucket, needs_review, evidence_summary, "
                " normalized_value_json, raw_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record_id, area, poi_name,
                    asp.get("aspect_type", ""),
                    asp.get("sentiment", "mixed"),
                    float(asp.get("confidence") or 0.5),
                    "general",
                    1,  # 用户上传的需要 review
                    asp.get("evidence_summary", ""),
                    json.dumps(nv, ensure_ascii=False),
                    json.dumps({**asp, "source_platform": source_platform},
                               ensure_ascii=False),
                ),
            )
            inserted += 1
        except sqlite3.Error:
            continue
    conn.commit()
    conn.close()
    return inserted


def upload_and_index(
    image_bytes: bytes,
    image_mime: str = "image/jpeg",
    client: Optional[LLMClient] = None,
) -> tuple[dict, int]:
    """一站式：抽取 + 落库。返回 (抽取结果, 入库条数)。"""
    extracted = extract_from_image(image_bytes, image_mime, client)
    n = persist_to_db(extracted)
    return extracted, n
