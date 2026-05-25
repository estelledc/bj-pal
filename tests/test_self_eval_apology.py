"""P0.5 验收：第二次同 POI 预测必显示前次反馈 + apology_card 可生成。

来源：USER_RESEARCH_FINDINGS 信号 3（5/5 一致：选错容忍度 = 2 次）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools.availability_probe import probe  # noqa: E402
from tools.mock_message import apology_card  # noqa: E402
from tools.prediction_log import (  # noqa: E402
    clear_history,
    get_last_error,
    record_actual,
    record_prediction,
)
from tools.types import POI  # noqa: E402


def _make_poi(name: str = "测试小馆") -> POI:
    return POI(
        id="t-1", name=name, category_lv1="餐饮服务",
        category_lv2=None, category_lv3=None, typecode=None,
        district=None, business_area=None, address=None,
        longitude=None, latitude=None, rating=4.3, avg_price=80,
        open_time=None, phone=None, photos=[],
    )


def t1_no_history_no_marker():
    """首次预测：无 last_prediction_error。"""
    clear_history("测试小馆_t1")
    poi = _make_poi("测试小馆_t1")
    r = probe(poi, party_size=2, target_time="14:00", seed=1)
    print(f"\n[1] 首次 wait={r.wait_min} conf={r.confidence} last_err={r.last_prediction_error}")
    assert r.last_prediction_error is None
    assert r.confidence == 0.8
    assert not any("上次预测偏差" in e for e in r.evidence)
    return r


def t2_after_error_marker_appears():
    """记 1 次预测 + 回填实际值偏差 30min → 第二次必显示警告。"""
    name = "测试小馆_t2"
    clear_history(name)
    record_prediction(name, "14:00", predicted_wait_min=10, confidence=0.8)
    record_actual(name, actual_wait_min=45, target_time="14:00")  # 偏差 35min
    err = get_last_error(name)
    print(f"\n[2] last_error: {err}")
    assert err is not None and err["error_min"] == 35

    poi = _make_poi(name)
    r = probe(poi, party_size=2, target_time="14:30", seed=1)
    print(f"    第二次 wait={r.wait_min} conf={r.confidence}")
    print(f"    evidence[0]: {r.evidence[0]}")
    assert r.last_prediction_error is not None
    assert r.last_prediction_error["error_min"] == 35
    assert r.confidence == 0.4   # 30-60min 偏差 → 0.4
    assert "上次预测偏差" in r.evidence[0]
    return r


def t3_small_error_no_marker():
    """偏差 < 15min 不算"上次错了"。"""
    name = "测试小馆_t3"
    clear_history(name)
    record_prediction(name, "14:00", predicted_wait_min=15, confidence=0.8)
    record_actual(name, actual_wait_min=20, target_time="14:00")  # 偏差 5min
    err = get_last_error(name)
    print(f"\n[3] 小偏差 last_error: {err}")
    assert err is None
    return True


def t4_apology_card_text():
    """apology_card 文案包含关键信息。"""
    card = apology_card(
        contact="老婆",
        poi_name="雍和宫",
        last_predicted="排队 30min",
        actual_observed="实际 85min",
        new_confidence=0.4,
        suggestion="改去国子监，UGC 反馈周末人流分散",
    )
    print(f"\n[4] apology body:\n{card.body}")
    assert "雍和宫" in card.body
    assert "0.4" in card.body
    assert "国子监" in card.body
    assert any(a["action"] == "ack_apology" for a in card.actions)
    return card


def t5_severe_error_drops_to_03():
    """偏差 ≥ 60min → confidence 降到 0.3。"""
    name = "测试小馆_t5"
    clear_history(name)
    record_prediction(name, "14:00", predicted_wait_min=10, confidence=0.8)
    record_actual(name, actual_wait_min=85, target_time="14:00")  # 偏差 75min
    poi = _make_poi(name)
    r = probe(poi, party_size=2, target_time="14:30", seed=1)
    print(f"\n[5] 严重偏差后 conf={r.confidence}")
    assert r.confidence == 0.3
    return r


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal P0.5 错误自承认 Tests")
    print("=" * 60)
    suite = [
        ("no_history_no_marker", t1_no_history_no_marker),
        ("after_error_marker", t2_after_error_marker_appears),
        ("small_error_no_marker", t3_small_error_no_marker),
        ("apology_card_text", t4_apology_card_text),
        ("severe_error_03", t5_severe_error_drops_to_03),
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
    print("✓ P0.5 验收 OK")
