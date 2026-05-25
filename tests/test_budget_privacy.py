"""P0.3 验收：群发卡片不能出现具体预算金额。

来源：USER_RESEARCH_FINDINGS 信号 7（5/5 一致：预算绝对私密）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.types import Plan, Step  # noqa: E402
from tools.mock_message import (  # noqa: E402
    DEMO_FRIEND_GROUP,
    _price_band,
    _scrub_budget_text,
    broadcast_to_group,
    render_im_card,
)


def _make_plan_with_prices() -> Plan:
    """造一份含具体金额的 plan。"""
    return Plan(
        persona="friends",
        area_anchor="三里屯",
        steps=[
            Step(step_index=0, poi_name="三里屯太古里", start_time="14:00",
                 kind="citywalk", rationale="人流量大，¥0"),
            Step(step_index=1, poi_name="京兆尹（雍和宫店）", start_time="15:30",
                 kind="meal", rationale="人均 ¥220 高品质素食",
                 booking={"avg_price": 220}),
            Step(step_index=2, poi_name="Cafe Zarah", start_time="17:00",
                 kind="rest", rationale="咖啡 ¥45 一杯"),
        ],
        summary="三里屯下午方案，预算 ¥250 / 人",
    )


def t1_scrub_helper():
    """_scrub_budget_text 把 ¥xxx 替换为档位标签。"""
    s = "人均 ¥220 高品质素食，咖啡 ¥45 一杯"
    out = _scrub_budget_text(s)
    print(f"\n[1] before: {s}")
    print(f"    after:  {out}")
    assert "¥220" not in out
    assert "¥45" not in out
    assert "¥" not in out
    return out


def t2_price_band_mapping():
    """_price_band 把金额映射到档位。"""
    cases = [(30, "亲民档"), (88, "中等档位"), (220, "中高档"), (966, "高档位")]
    for amt, expected in cases:
        got = _price_band(amt)
        print(f"    ¥{amt} → {got}")
        assert got == expected, f"{amt} 期望 {expected} 实际 {got}"
    return len(cases)


def t3_render_friend_card_no_money():
    """audience=friend 时卡片不应出现 ¥xxx。"""
    p = _make_plan_with_prices()
    card = render_im_card(p, audience="friend")
    print(f"\n[3] friend card body:\n{card.body}")
    assert "¥" not in card.body, f"friend 卡片仍含 ¥：{card.body}"
    assert "¥" not in card.plan_summary
    return card


def t4_render_spouse_card_keeps_money():
    """audience=spouse 时仍可保留具体金额（私聊不脱敏）。"""
    p = _make_plan_with_prices()
    card = render_im_card(p, audience="spouse")
    print(f"\n[4] spouse card body:\n{card.body}")
    # spouse 模式应保留金额（私聊场景）
    assert "¥" in card.body or "220" in card.body, "spouse 模式应保留金额"
    return card


def t5_broadcast_double_scrubs():
    """即使误传 spouse 卡片给 broadcast，也应被强制脱敏。"""
    p = _make_plan_with_prices()
    card = render_im_card(p, audience="spouse")  # 故意走 spouse
    assert "¥" in card.body, "前置：spouse 卡确实含 ¥"
    results = broadcast_to_group(card, DEMO_FRIEND_GROUP)
    print(f"\n[5] broadcast 后 card.body:\n{card.body}")
    assert "¥" not in card.body, "broadcast 必须强制脱敏"
    assert all(r.delivered for r in results)
    return len(results)


def t6_summary_with_budget_scrubbed():
    """summary 里 '预算 ¥250' 也要脱敏。"""
    p = _make_plan_with_prices()
    card = render_im_card(p, audience="group")
    print(f"\n[6] group card summary: {card.plan_summary}")
    assert "¥" not in card.plan_summary
    assert "档" in card.body or "档" in card.plan_summary, "应有档位标签替代"
    return card.plan_summary


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal P0.3 群发预算脱敏 Tests")
    print("=" * 60)
    suite = [
        ("scrub_helper", t1_scrub_helper),
        ("price_band", t2_price_band_mapping),
        ("friend_no_money", t3_render_friend_card_no_money),
        ("spouse_keeps_money", t4_render_spouse_card_keeps_money),
        ("broadcast_double_scrubs", t5_broadcast_double_scrubs),
        ("summary_scrubbed", t6_summary_with_budget_scrubbed),
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
    print("✓ P0.3 验收 OK")
