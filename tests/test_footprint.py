"""P1.1 验收：北京下午足迹数据聚合。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.footprint import (  # noqa: E402
    FootprintEntry,
    cumulative_stats,
    fetch_recent_sessions,
)
from tools.tool_call_log import (  # noqa: E402
    clear_session,
    init_log_db,
    log_call,
    set_session,
)


def t1_basic_aggregation():
    """造一个 session 的 mock 调用，footprint 应聚合出 reroute 数 + 下单成功数。"""
    init_log_db()
    sid = "test_footprint_001"
    clear_session(sid)
    set_session(sid)
    log_call("agents.planner.plan",
             params={"persona": "family", "area_anchor": "五道营-雍和宫片区"},
             response={"persona": "family", "area_anchor": "五道营-雍和宫片区",
                       "steps": [{}, {}, {}, {}, {}]},
             status="ok", latency_ms=300)
    log_call("agents.replanner.replan_step",
             params={"failed_idx": 0},
             response={"reason": "queue_85min"},
             status="ok", latency_ms=18)
    log_call("agents.replanner.replan_step",
             params={"failed_idx": 2},
             response={"reason": "weather"},
             status="ok", latency_ms=15)
    log_call("mock_book.book_restaurant",
             params={}, response={"status": "confirmed"},
             status="ok", latency_ms=600)
    log_call("mock_book.book_restaurant",
             params={}, response={"status": "no_availability"},
             status="ok", latency_ms=400)
    log_call("mock_message.broadcast_to_group",
             params={}, response={"sent": 4}, status="ok", latency_ms=20)
    log_call("mock_message.simulate_group_responses",
             params={}, response={"rejected": 1}, status="ok", latency_ms=15)

    sessions = [s for s in fetch_recent_sessions(limit=20) if s.session_id == sid]
    assert sessions, "应找到 session"
    e = sessions[0]
    print(f"\n[1] session={e.session_id}")
    print(f"    persona={e.persona} area={e.area_anchor}")
    print(f"    steps={e.plan_steps} reroute={e.reroute_count} reasons={e.reroute_reasons}")
    print(f"    booking ok/fail={e.booking_success}/{e.booking_failed}")
    print(f"    group ✓/✗={e.group_confirmed}/{e.group_rejected}")
    print(f"    summary: {e.summary_zh}")
    assert e.persona == "family"
    assert e.area_anchor == "五道营-雍和宫片区"
    assert e.plan_steps == 5
    assert e.reroute_count == 2
    assert e.reroute_reasons.get("queue") == 1
    assert e.reroute_reasons.get("weather") == 1
    assert e.booking_success == 1
    assert e.booking_failed == 1
    assert e.group_rejected == 1
    assert e.summary_zh
    return e


def t2_cumulative_stats():
    """累计指标可读。"""
    s = cumulative_stats()
    print(f"\n[2] cumulative: {s}")
    assert "total_sessions" in s
    assert "total_reroutes" in s
    assert "total_bookings" in s
    assert s["total_sessions"] >= 1
    return s


def t3_empty_session():
    """空 session 返回基础 entry 不崩。"""
    sid = "test_empty_session"
    set_session(sid)
    sessions = fetch_recent_sessions(limit=20)
    targets = [s for s in sessions if s.session_id == sid]
    print(f"\n[3] empty session entries: {len(targets)}")
    # 没有 log_call，应找不到（DISTINCT session_id 来自 tool_calls 表）
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal P1.1 北京下午足迹 Tests")
    print("=" * 60)
    suite = [
        ("aggregation", t1_basic_aggregation),
        ("cumulative_stats", t2_cumulative_stats),
        ("empty_session", t3_empty_session),
    ]
    failed = []
    for name, fn in suite:
        try:
            fn()
        except AssertionError as e:
            failed.append((name, str(e)))
            print(f"    ✗ {e}")
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))
            import traceback; traceback.print_exc()
    print("\n" + "=" * 60)
    if failed:
        print(f"✗ {len(failed)} 项失败")
        for n, m in failed:
            print(f"  - {n}: {m}")
        sys.exit(1)
    print("✓ P1.1 验收 OK")
