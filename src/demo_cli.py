"""W1 D7 / W2 D1 e2e CLI demo。

跑法：
    python3 src/demo_cli.py
    python3 src/demo_cli.py --persona friends --area "王府井-东单片区"
    BJ_PAL_LLM=longcat python3 src/demo_cli.py     # 真 LongCat
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents.planner import plan as make_plan  # noqa: E402
from agents.replanner import probe_plan  # noqa: E402
from agents.types import UserPreferences  # noqa: E402
from tools.mock_book import book_cake_delivery, book_restaurant  # noqa: E402
from tools.mock_message import render_im_card, send_via_wechat_mock  # noqa: E402
from tools.tool_call_log import clear_session, fetch_calls, set_session  # noqa: E402


SCENARIOS = {
    "family": {
        "user_input": "今天下午带老婆和 5 岁娃出去玩，别离家太远，4 小时左右。老婆减脂，娃喜欢动物。",
        "prefs": UserPreferences(
            persona="family",
            party_size=3,
            has_child=True,
            child_age=5,
            diet_flags=["light_diet"],
            walk_radius_km=1.5,
            budget_per_person=120,
            target_start="14:00",
            duration_hours=4.5,
        ),
        "audience": "spouse",
        "contact": "老婆",
    },
    "friends": {
        "user_input": "跟 4 个朋友周六下午出去玩，2 男 2 女，别太赶，能聊天。",
        "prefs": UserPreferences(
            persona="friends",
            party_size=4,
            walk_radius_km=2.0,
            budget_per_person=250,
            target_start="14:30",
            duration_hours=5.0,
        ),
        "audience": "friend",
        "contact": "@小张",
    },
}


def banner(text: str, char: str = "="):
    print(char * 70)
    print(f"  {text}")
    print(char * 70)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", choices=["family", "friends"], default="family")
    parser.add_argument("--area", default="五道营-雍和宫片区")
    parser.add_argument("--book", action="store_true", help="实际触发 mock 下单")
    parser.add_argument("--with-cake", action="store_true",
                        help="在 meal 步骤顺带订蛋糕")
    args = parser.parse_args()

    session_id = f"demo-{uuid.uuid4().hex[:8]}"
    set_session(session_id)
    clear_session(session_id)
    print(f"[session] {session_id}\n")

    sc = SCENARIOS[args.persona]

    # ============================================================
    # Stage 1: Plan
    # ============================================================
    banner("Stage 1 · Planner 生成方案 v1")
    print(f"用户输入：{sc['user_input']}\n")
    p1 = make_plan(
        user_input=sc["user_input"],
        persona=args.persona,
        prefs=sc["prefs"],
        area_anchor=args.area,
    )
    _print_plan(p1, label="v1 方案")

    # ============================================================
    # Stage 2: Probe & Reroute
    # ============================================================
    banner("Stage 2 · 主动余位探针 + Reroute", "-")
    p2, events = probe_plan(p1, prefs=sc["prefs"])
    if not events:
        print("✅ 无需 reroute，方案 v1 全程通畅")
    else:
        for ev in events:
            print(f"\n⚠️ 检测到风险：")
            print(f"   step #{ev.failed_step_idx}: {ev.failed_poi_name}")
            print(f"   原因：{ev.reason}")
            print(f"   evidence：")
            for e in ev.evidence[:2]:
                print(f"     • {e}")
            print(f"   → 已切换到：{ev.replacement_poi_name}")
        _print_plan(p2, label="v2 方案（reroute 后）")

    # ============================================================
    # Stage 3: Mock booking
    # ============================================================
    banner("Stage 3 · Mock 下单", "-")
    if args.book:
        for s in p2.steps:
            if s.kind == "meal" and s.poi_id:
                book = book_restaurant(
                    poi_id=s.poi_id, poi_name=s.poi_name,
                    target_time=s.start_time, party_size=sc["prefs"].party_size,
                    contact_name=sc["contact"],
                )
                _print_booking(book)
                if args.with_cake and book.status == "confirmed":
                    cake = book_cake_delivery(
                        restaurant_id=s.poi_id, restaurant_name=s.poi_name,
                        cake_spec="6 寸草莓奶油（无糖）",
                        delivery_time=s.start_time,
                        greeting_message="周末快乐❤️",
                    )
                    print(f"   🎂 蛋糕配送：{cake.status}（ETA {cake.eta_min}min）")
    else:
        print("（跳过实际下单；加 --book 开启）")

    # ============================================================
    # Stage 4: 话术化 IM 卡片
    # ============================================================
    banner("Stage 4 · 话术化 IM 卡片", "-")
    card = render_im_card(p2, audience=sc["audience"])
    print(f"\n[卡片标题] {card.title}")
    print(f"[发送对象] {sc['contact']}")
    print("[卡片正文]")
    print("-" * 50)
    print(card.body)
    print("-" * 50)
    print(f"[操作按钮] {[a['label'] for a in card.actions]}\n")

    if args.book:
        send = send_via_wechat_mock(card, sc["contact"])
        print(f"   📱 发送结果：delivered={send.delivered} msg_id={send.message_id}")

    # ============================================================
    # Stage 5: Tool Call Log
    # ============================================================
    banner("Stage 5 · Tool Call Log（评委 Q&A 用）", "-")
    calls = fetch_calls(session_id=session_id, limit=50)
    print(f"本 session {len(calls)} 次工具调用：\n")
    for c in reversed(calls[:20]):
        params_brief = (c["params_json"] or "")[:60]
        print(f"   {c['timestamp'][11:19]} [{c['tool_name']:30}] "
              f"{c['latency_ms']:.0f}ms  status={c['status']}  {params_brief}")

    banner(f"Demo 完成 · session={session_id}")


def _print_plan(p, label="方案"):
    print(f"\n📋 {label}（{p.persona}, {p.area_anchor}）：")
    for s in p.steps:
        marker = "🔄" if s.is_rerouted else " "
        print(f"   {marker} {s.step_index}. [{s.kind:9}] {s.start_time} {s.poi_name:25} "
              f"({s.duration_min}min, {s.mode_to_here})")
        if s.rationale:
            r = s.rationale[:100]
            print(f"        💭 {r}")
    if p.summary:
        print(f"   📝 总结：{p.summary}")


def _print_booking(b):
    icon = {"confirmed": "✅", "no_availability": "❌", "timeout": "⏱️",
            "rejected_by_merchant": "🚫"}.get(b.status, "?")
    print(f"\n{icon} {b.poi_name}（{b.party_size} 人 @ {b.target_time}）")
    print(f"   status={b.status}")
    if b.message:
        print(f"   {b.message}")
    if b.confirmation_url:
        print(f"   预订链接：{b.confirmation_url}")


if __name__ == "__main__":
    main()
