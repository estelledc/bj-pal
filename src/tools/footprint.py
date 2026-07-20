"""我的北京下午足迹（P1.1）。

来源：USER_RESEARCH_FINDINGS Session C Q3（数据沉淀、迁移成本高、日常习惯是付费留存关键）

设计：
- 只读取独立 tool-audit runtime store 中的 v2 projected events，聚合每个 session 的：
  - 用了哪个片区 / persona / 大致时间
  - reroute 次数 + 触发原因分布
  - 群发响应（confirmed/rejected count）
  - mock_book 成功率
- 输出 FootprintEntry[] 给 UI 渲染历史时间线
- 形成可回看的连续使用记录
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from typing import Optional

from . import tool_call_log


@dataclass
class FootprintEntry:
    session_id: str
    started_at: str = ""              # 该 session 第一条 tool_call 时间
    ended_at: str = ""                # 最后一条
    persona: str = ""
    area_anchor: str = ""
    plan_steps: int = 0               # planner 输出的 step 数
    reroute_count: int = 0
    reroute_reasons: dict = field(default_factory=dict)   # {"queue": 1, "weather": 0}
    booking_success: int = 0
    booking_failed: int = 0
    group_confirmed: int = 0
    group_rejected: int = 0
    apology_card_count: int = 0
    summary_zh: str = ""              # 一句话汇总


def fetch_recent_sessions(limit: int = 10) -> list[FootprintEntry]:
    """聚合最近 N 个 session 的足迹。"""
    tool_call_log.init_log_db()
    database = tool_call_log.database_path()
    if not database.exists():
        return []
    with closing(sqlite3.connect(database)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT calls.session_id, MAX(calls.id) AS latest_id
            FROM tool_calls AS calls
            WHERE calls.session_id IS NOT NULL
              AND calls.tool_name != 'audit.session_reset'
              AND calls.privacy_version = 'tool_call_audit_v2'
              AND calls.id > COALESCE((
                  SELECT MAX(reset.id)
                  FROM tool_calls AS reset
                  WHERE reset.session_id = calls.session_id
                    AND reset.tool_name = 'audit.session_reset'
                    AND reset.privacy_version = 'tool_call_audit_v2'
              ), 0)
            GROUP BY calls.session_id
            ORDER BY latest_id DESC
            """
        ).fetchall()
        sessions = [r["session_id"] for r in rows][:limit]

    return [_aggregate_session(s) for s in sessions]


def _aggregate_session(session_id: str) -> FootprintEntry:
    entry = FootprintEntry(session_id=session_id)
    with closing(sqlite3.connect(tool_call_log.database_path())) as conn:
        conn.row_factory = sqlite3.Row
        reset_row = conn.execute(
            """
            SELECT COALESCE(MAX(id), 0) AS reset_id
            FROM tool_calls
            WHERE session_id=? AND tool_name='audit.session_reset'
              AND privacy_version='tool_call_audit_v2'
            """,
            (session_id,),
        ).fetchone()
        reset_id = int(reset_row["reset_id"] or 0)
        rows = conn.execute(
            """
            SELECT * FROM tool_calls
            WHERE session_id=? AND id>? AND tool_name!='audit.session_reset'
              AND privacy_version='tool_call_audit_v2'
            ORDER BY id ASC
            """,
            (session_id, reset_id),
        ).fetchall()
    if not rows:
        return entry

    entry.started_at = rows[0]["timestamp"] or ""
    entry.ended_at = rows[-1]["timestamp"] or ""

    for r in rows:
        tool = r["tool_name"]
        params = _safe_json(r["params_json"])
        response = _safe_json(r["response_json"])

        if tool == "agents.planner.plan" or tool == "planner.plan":
            steps = response.get("steps") if isinstance(response, dict) else None
            if isinstance(steps, list):
                entry.plan_steps = len(steps)
            persona = (params or {}).get("persona") or (response or {}).get("persona")
            if persona:
                entry.persona = persona
            area = (params or {}).get("area_anchor") or (response or {}).get("area_anchor")
            if area:
                entry.area_anchor = area

        if tool == "agents.replanner.replan_step" or "replan" in (tool or ""):
            entry.reroute_count += 1
            reason = (response or {}).get("reason") or "unknown"
            # 对 'queue_85min' / 'queue_no_alt' 这种字符串归一
            base_reason = reason.split("_")[0] if isinstance(reason, str) else "unknown"
            entry.reroute_reasons[base_reason] = entry.reroute_reasons.get(base_reason, 0) + 1

        if tool == "mock_book.book_restaurant":
            status = (response or {}).get("status")
            if status == "confirmed":
                entry.booking_success += 1
            elif status:
                entry.booking_failed += 1

        if tool == "mock_message.simulate_group_responses":
            # response 简化结构 {'rejected': N}
            entry.group_rejected += int((response or {}).get("rejected", 0))

        if tool == "mock_message.broadcast_to_group":
            sent = int((response or {}).get("sent", 0))
            # 没记 confirmed 数，只能近似为 sent - rejected
            entry.group_confirmed += max(0, sent - entry.group_rejected)

        if tool == "mock_message.apology_card" or "apology" in (tool or "").lower():
            entry.apology_card_count += 1

    entry.summary_zh = _build_summary(entry)
    return entry


def _build_summary(e: FootprintEntry) -> str:
    bits = []
    if e.area_anchor:
        bits.append(f"在 {e.area_anchor[:8]}")
    if e.plan_steps:
        bits.append(f"{e.plan_steps} 站方案")
    if e.reroute_count:
        reasons = ", ".join(f"{k}({v})" for k, v in e.reroute_reasons.items())
        bits.append(f"reroute {e.reroute_count} 次：{reasons}")
    if e.booking_success or e.booking_failed:
        bits.append(f"下单 {e.booking_success}/{e.booking_success + e.booking_failed} 成")
    if e.group_confirmed or e.group_rejected:
        bits.append(f"群投 ✓{e.group_confirmed}/✗{e.group_rejected}")
    return "；".join(bits) or "（无明显事件）"


def _safe_json(text: Optional[str]) -> dict:
    if not text:
        return {}
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def cumulative_stats() -> dict:
    """全量累计指标——给 footprint 顶部"用了 X 次 / 改了 Y 站"用。"""
    tool_call_log.init_log_db()
    database = tool_call_log.database_path()
    if not database.exists():
        return {"total_sessions": 0, "total_reroutes": 0, "total_bookings": 0}
    with closing(sqlite3.connect(database)) as conn:
        conn.row_factory = sqlite3.Row
        sess = conn.execute(
            "SELECT COUNT(DISTINCT session_id) c FROM tool_calls "
            "WHERE privacy_version='tool_call_audit_v2'"
        ).fetchone()["c"] or 0
        rer = conn.execute(
            "SELECT COUNT(*) c FROM tool_calls WHERE tool_name LIKE '%replan%' "
            "AND privacy_version='tool_call_audit_v2'"
        ).fetchone()["c"] or 0
        bk = conn.execute(
            "SELECT COUNT(*) c FROM tool_calls "
            "WHERE tool_name='mock_book.book_restaurant' "
            "AND privacy_version='tool_call_audit_v2'"
        ).fetchone()["c"] or 0
    return {
        "total_sessions": sess,
        "total_reroutes": rer,
        "total_bookings": bk,
    }
