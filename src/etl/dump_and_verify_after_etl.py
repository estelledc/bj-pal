"""ETL 完成后：dump UGC → aspects.jsonl + 验证派生信号扩展。"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import get_conn  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "data" / "ugc" / "aspects.jsonl"


def dump_all():
    backup = OUT.with_suffix(".jsonl.bak-r16")
    if OUT.exists():
        shutil.copy(OUT, backup)
        print(f"备份 {OUT.name} → {backup.name}")

    conn = get_conn()
    rows = conn.execute("SELECT * FROM ugc_aspects").fetchall()
    print(f"SQLite 总条数: {len(rows)}")

    written = 0
    with OUT.open("w", encoding="utf-8") as f:
        for r in rows:
            d = dict(r)
            try:
                raw = json.loads(d.pop("raw_json", "{}") or "{}")
            except Exception:
                raw = {}
            try:
                nv = json.loads(d.pop("normalized_value_json", "{}") or "{}")
            except Exception:
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
    print(f"写入 {written} 条 → {OUT}")
    print(f"文件大小：{OUT.stat().st_size / 1024:.1f} KB")
    return written


def verify_signals():
    print("\n=== 派生信号重建（在新数据集上）===")
    from tools.facilities import build_index as fb_idx
    from tools.audience_segment import build_index as ab_idx
    from tools.seasonal import build_index as sb_idx
    from tools.ugc_bm25 import build_index as bm_idx
    from tools.wait_predictor import build_histogram as wp_idx
    from tools.poi_graph import build_graph as pg_idx

    # build_index 都从 SQLite 读，所以扩充后会立刻反映
    t0 = time.time()
    print(f"facility: {fb_idx(force_rebuild=True)} POI ({time.time()-t0:.1f}s)")
    t0 = time.time()
    print(f"audience: {ab_idx(force_rebuild=True)} POI ({time.time()-t0:.1f}s)")
    t0 = time.time()
    print(f"seasonal: {sb_idx(force_rebuild=True)} POI ({time.time()-t0:.1f}s)")
    t0 = time.time()
    print(f"BM25:     {bm_idx(force_rebuild=True)} 文档 ({time.time()-t0:.1f}s)")
    t0 = time.time()
    print(f"等位:     {wp_idx(force_rebuild=True)} POI 有等位数据 ({time.time()-t0:.1f}s)")
    t0 = time.time()
    print(f"POI 图:   {pg_idx(force_rebuild=True)} 节点 ({time.time()-t0:.1f}s)")


def show_new_areas():
    """查所有 V3 片区里今天新加的（覆盖率从 0 → ?）。"""
    print("\n=== 今天新加的片区（按 UGC 数量倒序，top 20）===")
    conn = get_conn()
    rows = conn.execute("""
        SELECT area_anchor, COUNT(*) as n
        FROM ugc_aspects
        WHERE area_anchor LIKE '%片区V3'
        GROUP BY area_anchor
        ORDER BY n DESC
        LIMIT 30
    """).fetchall()
    for r in rows:
        print(f"  {r['area_anchor']:40s} {r['n']} 条")
    # 今天新加的（取 round 6+7 名字）
    today_areas_kw = [
        "上地", "五道口", "北沙滩", "海淀黄庄", "清华西门",
        "香山", "植物园", "八大处", "玉泉山", "北宫",
        "亮马桥", "燕莎", "朝阳公园", "麦子店", "三元桥",
        "天桥", "陶然亭", "大栅栏", "玉泉营", "南锣南口",
        "鸟巢", "国家大剧院", "工人体育场", "五棵松", "凯迪拉克",
        "什刹海西沿", "五道营深度", "烟袋斜街", "帽儿胡同", "后海北沿",
        "簋街", "牛街", "护国寺", "前门小吃", "便宜餐",
        "首都博物馆", "国家博物馆", "军事博物馆", "电影博物馆", "中国美术馆",
        "西单大悦城", "通州万达", "祥云小镇", "大兴荟聚", "购物广场",
        "樱花", "银杏", "雪景", "夏夜", "庙会",
        "中关村-知春路", "798艺术区", "朝阳门-工体", "国贸-CBD", "颐和园-万寿山", "鼓楼-钟楼",
    ]
    n_today = 0
    n_total = 0
    for r in rows:
        a = r["area_anchor"]
        if any(kw in a for kw in today_areas_kw):
            n_today += 1
        n_total += 1
    print(f"\n今天新加 {n_today} / 全 V3 片区 {n_total}")
    conn.close()


if __name__ == "__main__":
    written = dump_all()
    show_new_areas()
    verify_signals()
    print("\n=== 完成 ===")
