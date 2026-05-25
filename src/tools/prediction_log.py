"""预测自评日志（P0.5）。

来源：USER_RESEARCH_FINDINGS 信号 3（5/5 一致：选错容忍度 = 2 次）

设计：
- 每次 availability_probe 输出 wait_min 时调 record_prediction
- 用户反馈实际等位时调 record_actual
- 下次同 POI probe 前先查 get_last_error；若上次偏差 > 阈值，下次 probe 在 evidence
  顶部带"上次预测偏差 X 分钟"，confidence 自动降档
- mock_message.apology_card 也用这份数据生成认错卡片

存储：单独一张 SQLite 表，和 tool_calls 同库。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DB = ROOT / "tool_calls.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prediction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poi_name TEXT NOT NULL,
    target_time TEXT,
    predicted_wait_min INTEGER,
    predicted_at TEXT,
    actual_wait_min INTEGER DEFAULT NULL,
    actual_at TEXT DEFAULT NULL,
    confidence REAL DEFAULT 0.8
);
CREATE INDEX IF NOT EXISTS idx_pred_poi ON prediction_log(poi_name);
"""

# 偏差阈值：实际 vs 预测差 > 15 分钟视为"上次错了"
ERROR_THRESHOLD_MIN = 15


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(LOG_DB)
    conn.executescript(_SCHEMA)
    return conn


def record_prediction(
    poi_name: str,
    target_time: str,
    predicted_wait_min: int,
    confidence: float = 0.8,
) -> int:
    """记一次预测。返回行 id。"""
    with closing(_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO prediction_log(poi_name, target_time, predicted_wait_min, "
            "predicted_at, confidence) VALUES (?,?,?,?,?)",
            (poi_name, target_time, predicted_wait_min,
             datetime.now().isoformat(timespec="seconds"), confidence),
        )
        conn.commit()
        return cur.lastrowid


def record_actual(
    poi_name: str,
    actual_wait_min: int,
    target_time: Optional[str] = None,
) -> bool:
    """回填实际等位时长到最近一次预测。

    Returns:
        True if 找到匹配的预测并更新；False 否则
    """
    with closing(_conn()) as conn:
        if target_time:
            row = conn.execute(
                "SELECT id FROM prediction_log WHERE poi_name=? AND target_time=? "
                "AND actual_wait_min IS NULL ORDER BY id DESC LIMIT 1",
                (poi_name, target_time),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM prediction_log WHERE poi_name=? "
                "AND actual_wait_min IS NULL ORDER BY id DESC LIMIT 1",
                (poi_name,),
            ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE prediction_log SET actual_wait_min=?, actual_at=? WHERE id=?",
            (actual_wait_min, datetime.now().isoformat(timespec="seconds"), row[0]),
        )
        conn.commit()
        return True


def get_last_error(poi_name: str) -> Optional[dict]:
    """查最近一次"已回填实际值且偏差超阈值"的预测。

    Returns:
        {"predicted": int, "actual": int, "error_min": int,
         "days_ago": int, "predicted_at": str} 或 None
    """
    with closing(_conn()) as conn:
        row = conn.execute(
            "SELECT predicted_wait_min, actual_wait_min, predicted_at "
            "FROM prediction_log WHERE poi_name=? AND actual_wait_min IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (poi_name,),
        ).fetchone()
        if not row:
            return None
        predicted, actual, predicted_at = row
        error_min = abs(actual - predicted)
        if error_min < ERROR_THRESHOLD_MIN:
            return None
        try:
            days_ago = (datetime.now() - datetime.fromisoformat(predicted_at)).days
        except (ValueError, TypeError):
            days_ago = 0
        return {
            "predicted": predicted,
            "actual": actual,
            "error_min": error_min,
            "days_ago": days_ago,
            "predicted_at": predicted_at,
        }


def degraded_confidence(base_confidence: float, last_error: dict) -> float:
    """根据上次偏差降级当次 confidence。

    - 偏差 15-30min → 0.5
    - 偏差 30-60min → 0.4
    - 偏差 ≥ 60min → 0.3
    """
    err = last_error.get("error_min", 0)
    if err >= 60:
        return 0.3
    if err >= 30:
        return 0.4
    return 0.5


def clear_history(poi_name: Optional[str] = None) -> int:
    """测试用：清空（按 POI 或全清）。"""
    with closing(_conn()) as conn:
        if poi_name:
            cur = conn.execute("DELETE FROM prediction_log WHERE poi_name=?", (poi_name,))
        else:
            cur = conn.execute("DELETE FROM prediction_log")
        conn.commit()
        return cur.rowcount
