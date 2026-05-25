"""Task 1.2 时段画像补强：在 ugc_aspects 表加 weekend_afternoon_intensity 字段。

纯规则填充（不调 LLM，可重跑），基于现有 time_bucket + evidence_summary 关键词。
价值：ranking 公式可按 intensity 加权，"周六下午"画像有真证据支撑。

跑法：
    python3 src/etl/add_time_bucket_intensity.py [--dry-run] [--reset]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import get_conn  # noqa: E402

# 关键词正向 / 负向集合
WEEKEND_KEYWORDS = ["周末", "周六", "周日", "节假日", "假期"]
AFTERNOON_KEYWORDS = [
    "下午", "午后", "14:", "15:", "16:", "17:", "13:",
    "citywalk", "city walk", "city-walk",
]
NEGATIVE_KEYWORDS = [
    "工作日", "早上", "早晨", "清晨", "凌晨", "深夜", "宵夜",
    "夜场", "夜店", "晚餐", "9:00", "10:00", "11:",
    "20:", "21:", "22:", "23:",
]

# time_bucket → 基础 intensity 分数
BUCKET_BASE_SCORE = {
    "weekend_afternoon": 1.0,
    "general": 0.5,        # 默认中性
    "holiday": 0.6,        # 假期下午也算
    "meal_time": 0.4,      # 餐时不一定是下午
    "evening": 0.2,        # 黄昏 / 黄昏后
    "weekday_dinner": 0.1, # 工作日晚餐
    "unknown": 0.3,        # 默认有概率
}


def compute_intensity(time_bucket: str, evidence_summary: str) -> float:
    """根据 time_bucket + evidence keyword 算 weekend_afternoon_intensity。

    范围 [0.0, 1.0]，weekend_afternoon 直接 1.0；general 看关键词决定 0.4-0.85。
    """
    base = BUCKET_BASE_SCORE.get(time_bucket, 0.4)

    if time_bucket == "weekend_afternoon":
        return 1.0  # 直接命中，不用看关键词

    if not evidence_summary:
        return base

    text = evidence_summary
    has_weekend = any(k in text for k in WEEKEND_KEYWORDS)
    has_afternoon = any(k in text for k in AFTERNOON_KEYWORDS)
    has_negative = any(k in text for k in NEGATIVE_KEYWORDS)

    score = base

    # general / holiday / meal_time / unknown 桶可上调
    if time_bucket in ("general", "holiday", "meal_time", "unknown"):
        if has_weekend and has_afternoon:
            score = min(0.9, score + 0.40)
        elif has_weekend or has_afternoon:
            score = min(0.75, score + 0.25)

    # 任何桶 + 负向关键词都下调（深夜 / 工作日 / 晚场都减）
    if has_negative:
        score = max(0.05, score - 0.20)

    # evening / weekday_dinner 桶有时也命中下午（如"傍晚下午分界"）
    if time_bucket in ("evening", "weekday_dinner"):
        if has_afternoon and not has_negative:
            score = min(0.5, score + 0.15)

    return round(score, 3)


def add_column_if_not_exists(conn: sqlite3.Connection):
    cur = conn.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(ugc_aspects)")}
    if "weekend_afternoon_intensity" not in cols:
        cur.execute(
            "ALTER TABLE ugc_aspects "
            "ADD COLUMN weekend_afternoon_intensity REAL DEFAULT NULL"
        )
        conn.commit()
        print("  [+] 新加列 weekend_afternoon_intensity")
    else:
        print("  [=] 列 weekend_afternoon_intensity 已存在")


def main():
    ap = argparse.ArgumentParser(description="时段画像补强")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打分布，不入库")
    ap.add_argument("--reset", action="store_true",
                    help="重置所有 weekend_afternoon_intensity 为 NULL 再填")
    args = ap.parse_args()

    conn = get_conn()
    cur = conn.cursor()
    add_column_if_not_exists(conn)

    if args.reset and not args.dry_run:
        cur.execute("UPDATE ugc_aspects SET weekend_afternoon_intensity = NULL")
        conn.commit()
        print("  [reset] 已重置所有 intensity=NULL")

    rows = list(cur.execute(
        "SELECT record_id, time_bucket, evidence_summary "
        "FROM ugc_aspects"
    ))
    print(f"\n=== 处理 {len(rows)} 条 aspect ===")

    updates = []
    histogram = [0] * 11  # 0.0-0.1, 0.1-0.2, ..., 0.9-1.0+
    for record_id, tb, evidence in rows:
        intensity = compute_intensity(tb or "general", evidence or "")
        bucket_idx = min(10, int(intensity * 10))
        histogram[bucket_idx] += 1
        updates.append((intensity, record_id))

    print(f"\n=== intensity 分布直方图 ===")
    labels = ["0.0-0.1", "0.1-0.2", "0.2-0.3", "0.3-0.4", "0.4-0.5",
              "0.5-0.6", "0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0", "1.0+"]
    for i, label in enumerate(labels):
        bar = "█" * min(50, histogram[i] // 5)
        print(f"  {label}  {histogram[i]:>4}  {bar}")

    high = sum(histogram[7:])  # ≥ 0.7
    mid = sum(histogram[4:7])  # 0.4-0.7
    low = sum(histogram[:4])   # < 0.4
    print(f"\n  HIGH (≥0.7): {high} 条 — 周六下午强相关")
    print(f"  MID  (0.4-0.7): {mid} 条 — 中性")
    print(f"  LOW  (<0.4): {low} 条 — 不适合下午时段")

    if args.dry_run:
        print("\n[DRY RUN] 不写库")
        return

    cur.executemany(
        "UPDATE ugc_aspects SET weekend_afternoon_intensity = ? "
        "WHERE record_id = ?",
        updates,
    )
    conn.commit()
    n_filled = cur.execute(
        "SELECT COUNT(*) FROM ugc_aspects "
        "WHERE weekend_afternoon_intensity IS NOT NULL"
    ).fetchone()[0]
    print(f"\n[DONE] {n_filled} 条已填 intensity")
    conn.close()


if __name__ == "__main__":
    main()
