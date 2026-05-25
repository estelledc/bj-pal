"""Tool Call Log：所有 mock 工具调用 + agent 决策都落 SQLite。

UI 侧栏的 Trace Panel 直接 query 这张表。

使用：
    from tools.tool_call_log import log_call
    log_call("amap.search_pois", params={...}, response={...}, latency_ms=12)
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DB = ROOT / "tool_calls.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    timestamp TEXT,
    tool_name TEXT NOT NULL,
    params_json TEXT,
    response_json TEXT,
    status TEXT,
    latency_ms REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool ON tool_calls(tool_name);
"""

_SESSION_ID: Optional[str] = None


def init_log_db():
    conn = sqlite3.connect(LOG_DB)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()


def set_session(session_id: str):
    global _SESSION_ID
    _SESSION_ID = session_id


def log_call(
    tool_name: str,
    params: Optional[dict] = None,
    response: Optional[Any] = None,
    status: str = "ok",
    latency_ms: float = 0.0,
    error: Optional[str] = None,
):
    """同步写一条 tool call 日志。线程安全用 conn-per-call。"""
    init_log_db()
    conn = sqlite3.connect(LOG_DB)
    conn.execute(
        "INSERT INTO tool_calls(session_id, timestamp, tool_name, params_json, "
        "response_json, status, latency_ms, error) VALUES (?,?,?,?,?,?,?,?)",
        (
            _SESSION_ID,
            datetime.now().isoformat(timespec="seconds"),
            tool_name,
            json.dumps(params or {}, ensure_ascii=False, default=_json_default),
            json.dumps(response, ensure_ascii=False, default=_json_default)
            if response is not None else None,
            status,
            latency_ms,
            error,
        ),
    )
    conn.commit()
    conn.close()


@contextmanager
def timed_call(tool_name: str, params: Optional[dict] = None):
    """用法：
        with timed_call("amap.search_pois", params={...}) as record:
            result = ...
            record["response"] = result
    """
    record: dict = {"response": None, "status": "ok", "error": None}
    t0 = time.time()
    try:
        yield record
    except Exception as e:
        record["status"] = "error"
        record["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        log_call(
            tool_name=tool_name,
            params=params,
            response=record["response"],
            status=record["status"],
            latency_ms=(time.time() - t0) * 1000,
            error=record["error"],
        )


def fetch_calls(session_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    init_log_db()
    conn = sqlite3.connect(LOG_DB)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM tool_calls"
    params: list = []
    if session_id:
        sql += " WHERE session_id = ?"
        params.append(session_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_session(session_id: str):
    init_log_db()
    conn = sqlite3.connect(LOG_DB)
    conn.execute("DELETE FROM tool_calls WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()


def _json_default(o):
    """处理 dataclass / datetime / 其他不能直接 dumps 的对象。"""
    from dataclasses import asdict, is_dataclass
    if is_dataclass(o):
        return asdict(o)
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)
