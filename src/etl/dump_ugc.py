"""把 ugc_aspects 表全表 dump 成 jsonl 持久化（防止 db rebuild 数据丢失）。

跑法：
    python3 src/etl/dump_ugc.py            # 默认 dump 所有非 manual_v1 的扩展数据
    python3 src/etl/dump_ugc.py --all      # dump 全部（包括 manual_v1）

输出：data/ugc/expanded_v2.jsonl
loader.py 会从 manual_ugc_seed.jsonl + expanded_v2.jsonl 一并加载。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import get_conn  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "data" / "ugc" / "expanded_v2.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="包含 manual_v1（默认只 dump 扩展数据）")
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    cur = conn.cursor()
    if args.all:
        rows = cur.execute("SELECT * FROM ugc_aspects").fetchall()
    else:
        rows = cur.execute(
            "SELECT * FROM ugc_aspects "
            "WHERE raw_json NOT LIKE '%manual_ugc_seed_v1%'"
        ).fetchall()

    print(f"=== dump {len(rows)} 条 → {OUT} ===")
    written = 0
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            d = dict(r)
            # raw_json 已经是 JSON 字符串，需要解析后扁平化为顶层字段
            try:
                raw = json.loads(d.pop("raw_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                raw = {}
            try:
                nv = json.loads(d.pop("normalized_value_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                nv = {}
            row_out = {
                "record_id": d["record_id"],
                "area_anchor": d["area_anchor"],
                "poi_name": d["poi_name"],
                "aspect_type": d["aspect_type"],
                "sentiment": d["sentiment"],
                "confidence": d["confidence"],
                "time_bucket": d["time_bucket"],
                "needs_review": d["needs_review"],
                "evidence_summary": d["evidence_summary"],
                "normalized_value": nv,
                "weekend_afternoon_intensity": d.get("weekend_afternoon_intensity"),
                # 来源元数据，从 raw_json 摘出来
                "dataset_version": raw.get("dataset_version", "unknown_v2"),
                "source_platform": raw.get("source_platform", "unknown"),
                "source_urls": raw.get("source_urls"),
                "source_poi_basis": raw.get("source_poi_basis"),
                "extraction_status": raw.get("extraction_status"),
                "privacy_status": raw.get("privacy_status"),
                "raw_text_excerpt": raw.get("raw_text_excerpt"),
            }
            f.write(json.dumps(row_out, ensure_ascii=False) + "\n")
            written += 1
    conn.close()
    print(f"  写入 {written} 条")
    print(f"  文件大小：{OUT.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
