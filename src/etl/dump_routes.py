"""把 routes 表 estimated_v2 数据 dump 成 jsonl 持久化。

跑法：python3 src/etl/dump_routes.py
输出：data/amap/routes/expanded_v2.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import get_conn  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "data" / "amap" / "routes" / "expanded_v2.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="包含原 amap cache（默认只 dump est: 开头的）")
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    cur = conn.cursor()
    if args.all:
        rows = cur.execute("SELECT * FROM routes").fetchall()
    else:
        rows = cur.execute(
            "SELECT * FROM routes WHERE cache_key LIKE 'est:%'"
        ).fetchall()

    print(f"=== dump {len(rows)} 条 → {OUT} ===")
    written = 0
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            d = dict(r)
            try:
                raw = json.loads(d.pop("raw_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                raw = {}
            row_out = {
                "cache_key": d["cache_key"],
                "scene_id": d["scene_id"],
                "leg_id": d["leg_id"],
                "mode": d["mode"],
                "summary": {
                    "distance_m": d["distance_m"],
                    "duration_s": d["duration_s"],
                    "summary": d["summary"],
                },
                "request": raw.get("request", {}),
                "dataset_version": raw.get("dataset_version", "estimated_v2"),
                "method": raw.get("method", ""),
                "from_poi": raw.get("from_poi", ""),
                "to_poi": raw.get("to_poi", ""),
                "from_business_area": raw.get("from_business_area", ""),
                "to_business_area": raw.get("to_business_area", ""),
            }
            f.write(json.dumps(row_out, ensure_ascii=False) + "\n")
            written += 1
    conn.close()
    print(f"  写入 {written} 条")
    print(f"  文件大小：{OUT.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
