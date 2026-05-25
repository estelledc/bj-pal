"""v2 改 6A 验收：UGC 截图上传 + vision 抽取。

默认走 mock client（不联网）。
设 BJ_PAL_TEST_LONGCAT_VISION=1 同时跑 LongCat 真实 vision 调用。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.llm_client import get_llm_client  # noqa: E402
from agents.vision_extractor import extract_from_image, persist_to_db, upload_and_index  # noqa: E402
from loader import query_ugc  # noqa: E402

DATA_ROOT = ROOT / "data" / "ugc"


def t1_extract_with_mock():
    """mock client 抽取——不联网。"""
    sample_jpg = next(DATA_ROOT.glob("*.jpg"), None)
    assert sample_jpg, f"data/ugc/ 中应有 jpg 文件"
    image_bytes = sample_jpg.read_bytes()
    extracted = extract_from_image(image_bytes, image_mime="image/jpeg",
                                    client=get_llm_client("mock"))
    print(f"\n[1] mock vision 抽取 → area={extracted['area_anchor']} "
          f"poi={extracted['poi_name']} aspects={len(extracted['aspects'])}")
    for a in extracted["aspects"]:
        print(f"    - [{a['aspect_type']:12}] {a['sentiment']:8} conf={a['confidence']} "
              f"{a['evidence_summary'][:40]}")
    assert len(extracted["aspects"]) >= 1
    assert "area_anchor" in extracted and "poi_name" in extracted
    return len(extracted["aspects"])


def t2_persist_to_db():
    """落 SQLite 后能查到。"""
    extracted = {
        "area_anchor": "TEST 片区",
        "poi_name": "TEST 测试餐厅",
        "aspects": [
            {"aspect_type": "food", "sentiment": "positive", "confidence": 0.88,
             "evidence_summary": "测试用例：菜品好吃",
             "normalized_value": {"taste_tags": ["test_tag"]}},
        ],
    }
    n = persist_to_db(extracted)
    print(f"\n[2] persist_to_db → {n} 条入库")
    rows = query_ugc(area_anchor="TEST")
    assert len(rows) >= 1, "查不到 TEST 测试 aspect"
    print(f"    查得：{rows[0]['record_id']} · {rows[0]['evidence_summary']}")
    # 清理
    import sqlite3
    from loader import DB
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM ugc_aspects WHERE area_anchor = ?", ("TEST 片区",))
    conn.commit()
    conn.close()
    return n


def t3_upload_and_index_e2e():
    """一站式：上传 → 抽取 → 入库 → 重新查得到。"""
    sample_jpg = next(DATA_ROOT.glob("*.jpg"), None)
    extracted, n = upload_and_index(sample_jpg.read_bytes(),
                                     client=get_llm_client("mock"))
    print(f"\n[3] upload_and_index → {n} 条新 aspect 入库")
    rows = query_ugc(area_anchor=extracted["area_anchor"][:6])
    assert len(rows) >= 1
    # 清理本次新加的（按 record_id 前缀）
    import sqlite3
    from loader import DB
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM ugc_aspects WHERE record_id LIKE 'upload_%'")
    conn.commit()
    conn.close()
    return n


def t4_longcat_vision_optional():
    if not os.environ.get("BJ_PAL_TEST_LONGCAT_VISION"):
        print("\n[4] LongCat vision 真调：跳过（设 BJ_PAL_TEST_LONGCAT_VISION=1 启用）")
        return None
    sample_jpg = next(DATA_ROOT.glob("*.jpg"), None)
    extracted = extract_from_image(sample_jpg.read_bytes(),
                                    client=get_llm_client("longcat"))
    print(f"\n[4] LongCat vision → area={extracted.get('area_anchor')} "
          f"aspects={len(extracted.get('aspects', []))}")
    for a in extracted["aspects"][:3]:
        print(f"    - [{a['aspect_type']}] {a['evidence_summary'][:60]}")
    return len(extracted["aspects"])


if __name__ == "__main__":
    print("=" * 60)
    print("BJ-Pal v2 改 6A Vision Extractor Tests")
    print("=" * 60)
    suite = [
        ("extract_with_mock", t1_extract_with_mock),
        ("persist_to_db", t2_persist_to_db),
        ("upload_and_index_e2e", t3_upload_and_index_e2e),
        ("longcat_vision_optional", t4_longcat_vision_optional),
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
    print("✓ v2 改 6A 验收 OK")
